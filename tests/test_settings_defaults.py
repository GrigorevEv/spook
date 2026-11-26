from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import httpx
import pytest

from spook.clock import ManualClock
from spook.fakes import FakeAudioInput, LoudnessSpeechDetector, ScriptedRecognizer
from spook.serve import make_server
from spook.settings import default_settings_path, load_settings
from spook.web import create_app

from conftest import SESSION_START, EventStream, StubProgram, free_port
from test_notes_screen import write_note
from test_settings_screen import Form


@dataclass
class ServiceWithoutSettingsFile:
    base_url: str
    home: Path
    config_home: Path
    clock: ManualClock
    notify: StubProgram
    opener: StubProgram
    http: httpx.Client = field(repr=False)

    def events(self) -> EventStream:
        return EventStream(self.http, f"{self.base_url}/events", 5.0)

    def page(self) -> str:
        return self.http.get(f"{self.base_url}/").text

    def wait_until(self, condition, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            assert time.monotonic() < deadline, "условие не наступило за отведённое время"
            time.sleep(0.01)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServiceWithoutSettingsFile]:
    home = tmp_path / "home"
    home.mkdir()
    programs = tmp_path / "bin"
    programs.mkdir()
    config_home = tmp_path / "config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", f"{programs}{os.pathsep}{os.environ['PATH']}")
    notify = StubProgram.create(tmp_path / "stubs", "notify")
    opener = StubProgram.create(tmp_path / "stubs", "open")
    (programs / "notify-send").symlink_to(notify.path)
    (programs / "xdg-open").symlink_to(opener.path)
    settings_path = default_settings_path()
    assert settings_path.is_relative_to(tmp_path) and not settings_path.exists()
    clock = ManualClock(SESSION_START)
    app = create_app(
        load_settings=lambda: load_settings(settings_path),
        audio_input=FakeAudioInput(),
        recognizer=ScriptedRecognizer(),
        clock=clock,
        state_dir=tmp_path / "state" / "spook",
        speech_detector=LoudnessSpeechDetector,
        settings_path=settings_path,
    )
    port = free_port()
    server = make_server(app, port=port, log_level="warning")
    thread = threading.Thread(target=server.run, name="spook-test-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert time.monotonic() < deadline, "служба не поднялась"
        time.sleep(0.01)
    try:
        with httpx.Client(timeout=5.0) as http:
            yield ServiceWithoutSettingsFile(
                base_url=f"http://127.0.0.1:{port}",
                home=home,
                config_home=config_home,
                clock=clock,
                notify=notify,
                opener=opener,
                http=http,
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_without_settings_file_screen_shows_default_folders_skills_models_efforts_auto_stop_and_terms_dictionary(service):
    response = service.http.get(f"{service.base_url}/settings")

    assert response.status_code == 200
    values = Form(response.text).values
    assert {key: values[key] for key in ("meeting.folder", "meeting.skill", "meeting.model", "meeting.effort")} == {
        "meeting.folder": str(service.home / "Documents/notes/работа"),
        "meeting.skill": "spook-meeting",
        "meeting.model": "gpt-6.2-astra",
        "meeting.effort": "high",
    }
    assert not any(key.startswith("lecture.") for key in values)
    assert values["auto_stop_minutes"] == "5"
    assert values["terms_dictionary"] == str(service.home / "Documents/notes/работа/Словарь терминов.md")
    assert (values["sources.system"], values["sources.mic"]) == ("", "")
    assert not service.config_home.exists()


def test_without_settings_file_auto_stop_comes_after_5_minutes_and_is_notified_through_notify_send(service):
    assert service.http.post(f"{service.base_url}/session/start").status_code == 200

    with service.events() as events:
        service.clock.advance(5 * 60 - 1)
        assert "идёт" in service.page()

        service.clock.advance(1)

        events.wait_for("state", containing="нет Сессии")

    service.wait_until(lambda: service.notify.calls())
    assert [call["argv"] for call in service.notify.calls()] == [["Сессия завершена", "5 минут без речи"]]
    assert not service.config_home.exists()


def test_without_settings_file_meeting_note_opens_in_obsidian_through_xdg_open(service):
    note = write_note(
        service.home / "Documents/notes/работа", "2026-11-05 10-00 Планёрка", "2026-11-05", "10:00", "10:30", "встреча"
    )

    response = service.http.post(f"{service.base_url}/notes/obsidian", data={"note": str(note)})

    assert response.status_code == 200, response.text
    assert [call["argv"] for call in service.opener.calls()] == [["obsidian://open?path=" + quote(str(note), safe="")]]
    assert not service.config_home.exists()
