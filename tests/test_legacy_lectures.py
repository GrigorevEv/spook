from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from conftest import SESSION_START, agent_model_and_effort
from test_notes_screen import command, notes_page, write_note
from test_settings_screen import save, settings_form

LECTURE_TEXT = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    "tags: [лекция]\n"
    'source: "https://example.org/raft"\n'
    "---\n"
    "\n"
    "## Транскрипт\n"
    "\n**0:00:00** Речь о Raft.\n"
)


def old_note(folder: Path, name: str, tags: str = "tags: [лекция]") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"2026-11-06 14-02 {name}.md"
    note.write_text(LECTURE_TEXT.replace("tags: [лекция]", tags), encoding="utf-8")
    return note


@pytest.mark.parametrize(
    ("shared_folder", "tags"),
    [
        (False, "tags: [лекция]"),
        (True, "tags: [Raft, 'лекция']"),
        (True, "tags:\n  - Raft\n  - лекция"),
    ],
    ids=["separate-folder", "shared-folder-inline-tags", "shared-folder-block-tags"],
)
def test_legacy_settings_and_note_are_preserved_and_only_meetings_can_be_opened_or_changed(spook, shared_folder, tags):
    folder = spook.meetings_dir if shared_folder else spook.wiki_dir / "lectures"
    old = old_note(folder, "Лекция", tags)
    before = old.read_bytes()
    with spook.settings_path.open("a", encoding="utf-8") as settings:
        settings.write(f'\n[lecture]\nfolder = "{folder}"\nskill = "my-old-skill"\neffort = "low"\n')
    meeting = write_note(
        spook.meetings_dir, "2026-11-05 10-00 Планёрка", "2026-11-05", "10:00", "10:30", "встреча, коллекция"
    )

    form = settings_form(spook).values
    assert form["meeting.folder"] == str(spook.meetings_dir)
    assert form["meeting.skill"] == "spook-meeting" and form["meeting.effort"] == "high"
    assert not any(key.startswith("lecture.") for key in form)
    assert save(spook, auto_stop_minutes="8", lecture__effort="max").status_code == 200
    saved = tomllib.loads(spook.settings_path.read_text(encoding="utf-8"))
    assert saved["lecture"] == {"folder": str(folder), "skill": "my-old-skill", "effort": "low"}

    page = notes_page(spook)
    assert str(meeting) in page and str(old) not in page
    assert spook.http.get(f"{spook.base_url}/notes/row", params={"note": str(meeting)}).text.strip().startswith("<tr")
    assert command(spook, "obsidian", old).status_code == 409
    assert command(spook, "agent", old, model="gpt-6.2-astra", effort="high", confirm="yes").status_code == 409
    assert command(spook, "wiki", old).status_code == 404
    assert spook.http.post(f"{spook.base_url}/agent/topic", data={"note": str(old), "topic": "Другая"}).status_code == 409
    assert old.read_bytes() == before
    assert spook.opener.calls() == [] and spook.agent.calls() == []


def queued_job(note: Path, kind: str, state: str, snapshot: str | None = None) -> dict:
    return {
        "note": str(note),
        "kind": kind,
        "skill": f"spook-{kind}",
        "model": "opus",
        "effort": "medium",
        "cwd": str(note.parent),
        "state": state,
        "snapshot": snapshot,
    }


def test_restart_skips_old_session_and_agent_jobs_and_processes_queued_meeting(spook):
    folder = spook.wiki_dir / "lectures"
    running = old_note(folder, "Лекция")
    queued = old_note(folder, "Протокол Raft")
    meeting = write_note(spook.meetings_dir, "2026-11-05 10-00 Планёрка", "2026-11-05", "10:00", "10:30", "встреча")
    spook.state_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "note": str(running), "kind": "lecture", "topic": "", "named_topic": "", "link": "https://example.org/raft",
        "state": "running", "started_at": SESSION_START.isoformat(), "last_active_at": SESSION_START.isoformat(),
        "captured_seconds": 0, "paused_at": None, "last_speech_at": None,
    }
    (spook.state_dir / "session.json").write_text(json.dumps(record), encoding="utf-8")
    journals = spook.state_dir / "agent"
    journals.mkdir(exist_ok=True)
    previous_snapshot = LECTURE_TEXT.replace("Речь о Raft.", "Прежний текст.")
    jobs = [
        queued_job(running, "lecture", "running", previous_snapshot),
        queued_job(meeting, "meeting", "queued"),
        queued_job(queued, "lecture", "queued"),
    ]
    (journals / "queue.json").write_text(json.dumps(jobs), encoding="utf-8")
    spook.agent.play("summary")

    spook.restart_service(60)

    spook.wait_until(lambda: spook.notify.calls())
    [call] = spook.agent.calls()
    assert call["input"]["topic"] == "Планёрка"
    assert agent_model_and_effort(call) == ("gpt-6.2-astra", "high")
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", meeting.stem]]
    assert "нет Сессии" in spook.page().text
    assert spook.audio.active_sources == set()
    assert not (journals / "lecture").exists()
    assert running.read_bytes() == queued.read_bytes() == LECTURE_TEXT.encode("utf-8")
    assert spook.start_meeting().status_code == 200
