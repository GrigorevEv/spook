from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

import httpx
import numpy as np
import pytest
import uvicorn

from spook.audio import AudioInput, Source
from spook.clock import ManualClock
from spook.cutting import SpeechDetectorFactory
from spook.fakes import FakeAudioInput, LoudnessSpeechDetector, ScriptedRecognizer
from spook.recognizer import Recognizer
from spook.serve import make_server
from spook.settings import Settings, load_settings
from spook.web import create_app

pytest_plugins = ("chrome",)

SAMPLE_RATE = 16_000
NO_SPEECH = ""
SESSION_START = datetime(2026, 11, 6, 14, 2, tzinfo=timezone(timedelta(hours=3)))
STUBS_DIR = Path(__file__).parent / "stubs"

NOTE_AFTER_START = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    "tags: [встреча]\n"
    "---\n"
    "\n"
    "## Транскрипт\n"
)


def note_text(spook: "Service") -> str:
    return (spook.meetings_dir / "2026-11-06 14-02 Встреча.md").read_text(encoding="utf-8")


def agent_model_and_effort(call: dict) -> tuple[str, str]:
    args = call["argv"]
    model = args[args.index("--model") + 1]
    [setting] = [arg for arg in args if arg.startswith("model_reasoning_effort=")]
    return model, json.loads(setting.split("=", 1)[1])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def speech(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(seed=int(seconds * 1000))
    return rng.uniform(-0.3, 0.3, int(seconds * SAMPLE_RATE)).astype(np.float32)


@dataclass
class SseEvent:
    name: str
    data: str


class EventStream:
    def __init__(self, client: httpx.Client, url: str, timeout: float) -> None:
        self._stream = client.stream("GET", url, timeout=timeout)
        self._timeout = timeout
        self._lines: Iterator[str] | None = None

    def __enter__(self) -> "EventStream":
        response = self._stream.__enter__()
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream")
        self._lines = response.iter_lines()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.__exit__(*exc)

    def next_event(self) -> SseEvent:
        assert self._lines is not None
        name, data = "message", []
        for line in self._lines:
            if line == "":
                if data:
                    return SseEvent(name, "\n".join(data))
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
        raise AssertionError("поток событий закончился")

    def wait_for(self, name: str, containing: str = "") -> SseEvent:
        overall_deadline = time.monotonic() + 4 * self._timeout
        while True:
            assert time.monotonic() < overall_deadline, f"событие {name} «{containing}» не пришло"
            event = self.next_event()
            if event.name == name and containing in event.data:
                return event


@dataclass
class StubProgram:
    name: str
    control: Path
    path: Path

    @classmethod
    def create(cls, control: Path, name: str) -> "StubProgram":
        control.mkdir(parents=True, exist_ok=True)
        path = control / name
        path.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{STUBS_DIR / f"{name}_stub.py"}" "{control}" "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return cls(name, control, path)

    def calls(self) -> list[dict]:
        record = self.control / f"{self.name}-calls.jsonl"
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]

    def play(self, scenario: str) -> None:
        (self.control / f"{self.name}-scenario").write_text(scenario, encoding="utf-8")

    def release(self) -> None:
        (self.control / f"{self.name}-release").touch()


@dataclass
class Service:
    base_url: str
    settings: Settings
    settings_path: Path
    wiki_dir: Path
    meetings_dir: Path
    state_dir: Path
    skills_dir: Path
    audio: AudioInput
    recognizer: Recognizer
    clock: ManualClock
    agent: StubProgram
    notify: StubProgram
    opener: StubProgram
    http: httpx.Client = field(repr=False)
    restart_server: Callable[[], None] = field(repr=False)
    restart_service: Callable[[float], None] = field(repr=False)

    @property
    def programs(self) -> dict[str, str]:
        return {
            "codex": str(self.agent.path),
            "notify": str(self.notify.path),
            "open": str(self.opener.path),
        }

    def write_settings(self, **overrides) -> None:
        overrides.setdefault("agent", {}).setdefault("skills_dir", str(self.skills_dir))
        overrides.setdefault("meetings_dir", self.meetings_dir)
        overrides.setdefault("terms_dictionary", self.settings.terms_dictionary)
        write_settings(
            self.settings_path,
            self.settings.port,
            wiki_dir=self.wiki_dir,
            programs=self.programs,
            **overrides,
        )

    def page(self) -> httpx.Response:
        return self.http.get(f"{self.base_url}/")

    def feed_system(self, samples: np.ndarray) -> None:
        self.audio.feed(np.zeros_like(samples), Source.MIC)
        self.audio.feed(samples, Source.SYSTEM)

    def start_meeting(self, topic: str = "") -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/start", data={"topic": topic})

    def set_topic(self, topic: str) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/topic", data={"topic": topic})

    def pause(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/pause")

    def resume(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/resume")

    def stop(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/stop")

    def events(self, timeout: float = 5.0) -> EventStream:
        return EventStream(self.http, f"{self.base_url}/events", timeout)

    def wait_until(self, condition: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("условие не наступило за отведённое время")
            time.sleep(0.01)


def write_settings(
    path: Path,
    port: int,
    wiki_dir: Path,
    meetings_dir: Path,
    max_utterance_seconds: float | None = None,
    auto_stop_minutes: float | None = None,
    programs: dict[str, str] | None = None,
    meeting: dict[str, str] | None = None,
    recognition: dict[str, float] | None = None,
    terms_dictionary: Path | None = None,
    agent: dict[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meeting_lines = "".join(f'{key} = "{value}"\n' for key, value in (meeting or {}).items())
    recognition_keys = dict(recognition or {})
    if max_utterance_seconds is not None:
        recognition_keys["max_utterance_seconds"] = max_utterance_seconds
    recognition_lines = "".join(f"{key} = {value}\n" for key, value in recognition_keys.items())
    recognition_section = f"\n[recognition]\n{recognition_lines}" if recognition_lines else ""
    auto_stop = "" if auto_stop_minutes is None else f"auto_stop_minutes = {auto_stop_minutes}\n"
    terms = "" if terms_dictionary is None else f'terms_dictionary = "{terms_dictionary}"\n'
    program_lines = "".join(f'{key} = "{value}"\n' for key, value in (programs or {}).items())
    agent_lines = "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in (agent or {}).items())
    agent_section = f"\n[agent]\n{agent_lines}" if agent_lines else ""
    path.write_text(
        f"port = {port}\n"
        f'wiki = "{wiki_dir}"\n'
        f"{auto_stop}"
        f"{terms}"
        "\n"
        "[meeting]\n"
        f'folder = "{meetings_dir}"\n'
        f"{meeting_lines}"
        f"{recognition_section}"
        f"{agent_section}"
        "\n"
        "[programs]\n"
        f"{program_lines}",
        encoding="utf-8",
    )


@pytest.fixture
def audio_input() -> AudioInput:
    return FakeAudioInput()


@pytest.fixture
def speech_detector() -> SpeechDetectorFactory:
    return LoudnessSpeechDetector


@pytest.fixture
def recognizer() -> Recognizer:
    return ScriptedRecognizer()


@pytest.fixture
def spook(
    tmp_path: Path,
    audio_input: AudioInput,
    recognizer: Recognizer,
    speech_detector: SpeechDetectorFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Service]:
    settings_path = tmp_path / "config" / "spook" / "settings.toml"
    wiki_dir = tmp_path / "wiki"
    meetings_dir = tmp_path / "notes" / "работа"
    state_dir = tmp_path / "state" / "spook"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    skills_dir = tmp_path / "skills"
    skill = Path(__file__).resolve().parents[1] / "skills" / "spook-meeting"
    shutil.copytree(skill, skills_dir / skill.name)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    agent = StubProgram.create(tmp_path / "stubs", "codex")
    notify = StubProgram.create(tmp_path / "stubs", "notify")
    opener = StubProgram.create(tmp_path / "stubs", "open")
    programs = {"codex": str(agent.path), "notify": str(notify.path), "open": str(opener.path)}
    write_settings(
        settings_path,
        free_port(),
        wiki_dir=wiki_dir,
        programs=programs,
        meetings_dir=meetings_dir,
        terms_dictionary=meetings_dir.parent / "Словарь терминов.md",
        agent={"skills_dir": str(skills_dir)},
    )
    settings = load_settings(settings_path)

    def load_stubbed_settings() -> Settings:
        loaded = load_settings(settings_path)
        programs = loaded.programs
        for program in (programs.codex, programs.notify, programs.open):
            assert Path(program).is_relative_to(tmp_path), f"внешняя программа вне заглушек: {program}"
        assert loaded.terms_dictionary.is_relative_to(tmp_path), "Словарь терминов вне временного каталога"
        assert loaded.agent.skills_dir.is_relative_to(tmp_path), "Скиллы вне временного каталога"
        return loaded

    def build_app(audio: AudioInput, recognizer: Recognizer, clock: ManualClock):
        return create_app(
            load_settings=load_stubbed_settings,
            audio_input=audio,
            recognizer=recognizer,
            clock=clock,
            state_dir=state_dir,
            speech_detector=speech_detector,
            settings_path=settings_path,
        )

    audio = audio_input
    clock = ManualClock(SESSION_START)
    app = build_app(audio, recognizer, clock)

    def start_server() -> tuple[uvicorn.Server, threading.Thread]:
        server = make_server(app, port=settings.port, log_level="warning")
        thread = threading.Thread(target=server.run, name="spook-test-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline, "служба не поднялась"
            time.sleep(0.01)
        return server, thread

    def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "служба не остановилась"

    running = start_server()

    def restart_server() -> None:
        nonlocal running
        stop_server(*running)
        running = start_server()

    def restart_service(after_seconds: float) -> None:
        nonlocal running, app
        stop_server(*running)
        service.clock = ManualClock(service.clock.now() + timedelta(seconds=after_seconds))
        service.audio = FakeAudioInput()
        service.recognizer = ScriptedRecognizer()
        app = build_app(service.audio, service.recognizer, service.clock)
        running = start_server()

    with httpx.Client(timeout=5.0) as http:
        service = Service(
            base_url=f"http://127.0.0.1:{settings.port}",
            settings=settings,
            settings_path=settings_path,
            wiki_dir=wiki_dir,
            meetings_dir=meetings_dir,
            state_dir=state_dir,
            skills_dir=skills_dir,
            audio=audio,
            recognizer=recognizer,
            clock=clock,
            agent=agent,
            notify=notify,
            opener=opener,
            http=http,
            restart_server=restart_server,
            restart_service=restart_service,
        )
        yield service

    stop_server(*running)


@dataclass
class Demo:
    base_url: str
    process: subprocess.Popen = field(repr=False)
    http: httpx.Client = field(repr=False)

    def events(self, timeout: float = 15.0) -> EventStream:
        return EventStream(self.http, f"{self.base_url}/events", timeout)

    def terminate(self) -> int:
        if self.process.poll() is None:
            self.process.terminate()
        return self.process.wait(timeout=10)


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Demo]:
    port = free_port()
    log = (tmp_path / "demo.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "spook.cli", "demo", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert process.poll() is None, f"показ завершился с кодом {process.returncode}"
                assert time.monotonic() < deadline, "показ не поднялся"
                time.sleep(0.05)
        with httpx.Client(timeout=5.0) as http:
            yield Demo(base_url=f"http://127.0.0.1:{port}", process=process, http=http)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        log.close()
