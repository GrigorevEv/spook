from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import speech
from test_agent_launch import NOTE_FILE
from test_wisp_publication import entries
from wisp_page import read_page

UTTERANCE = "Лидер рассылает записи журнала."
TOPIC = "Репликация в Raft"
TITLE = "2026-11-06 14-02 Репликация в Raft"


def finished_meeting(spook, topic: str = "", scenario: str = "fail") -> Path:
    before = set(spook.pages())
    notifications = len(spook.notify.calls())
    spook.agent.play(scenario)
    spook.recognizer.say(UTTERANCE)
    assert spook.start_meeting(topic=topic).status_code == 200
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing=UTTERANCE)
        spook.stop()
        events.wait_for("agent", containing="ошибка" if scenario == "fail" else "готово")
    spook.wait_until(lambda: len(spook.notify.calls()) == notifications + 1)
    [note] = set(spook.pages()) - before
    return note


def topic_command(spook, note: Path, *topic: str) -> subprocess.CompletedProcess:
    environment = {
        "HOME": str(spook.settings_path.parents[2]),
        "PATH": os.environ.get("PATH", ""),
        "XDG_CONFIG_HOME": str(spook.settings_path.parents[1]),
    }
    return subprocess.run(
        [sys.executable, "-m", "spook.cli", "topic", str(note), *topic],
        cwd=note.parent,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_topic_command_sets_the_title_and_prints_the_existing_published_path(spook):
    note = finished_meeting(spook)
    before = read_page(note)

    completed = topic_command(spook, note, TOPIC)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{note}\n" and completed.stderr == ""
    assert spook.pages() == [note] and note.name == NOTE_FILE
    after = read_page(note)
    assert after.title == TITLE
    assert after.properties == {**before.properties, "topic": TOPIC}
    assert after.transcript == before.transcript
    assert TITLE in spook.page().text
    [entry] = entries(spook)
    assert entry["file"] == note.name and TOPIC in entry["summary"]


def test_spook_saves_agent_reply_under_stable_address_and_reports_the_topic(spook):
    note = finished_meeting(spook, scenario="topic")

    assert note.name == NOTE_FILE and spook.pages() == [note]
    page = read_page(note)
    assert page.title == TITLE and page.properties["topic"] == TOPIC
    assert page.summary == "Суть\n\nПро тесты."
    assert page.transcript == f"14:02 Собеседники: {UTTERANCE}"
    assert TITLE in spook.page().text
    [call] = spook.agent.calls()
    assert call["input"]["topic"] == ""
    assert call["input"]["transcript"] == f"**14:02 Собеседники:** {UTTERANCE}"
    [entry] = entries(spook)
    assert entry["file"] == note.name and TOPIC in entry["summary"]
    assert [path.name for path in (spook.state_dir / "agent/meeting").iterdir()] == [f"{note.stem}.log"]
    log = spook.http.get(f"{spook.base_url}/agent/log", params={"note": note.stem})
    assert log.status_code == 200 and "Выжимка: готово" in log.text and "Охрана Транскрипта" not in log.text
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", TITLE]]


def test_topic_command_is_rejected_while_session_with_this_draft_is_running(spook):
    spook.start_meeting()
    note = spook.current_note()
    before = note.read_bytes()

    completed = topic_command(spook, note, TOPIC)

    assert completed.returncode == 1 and not completed.stdout
    assert "Сессия с этой Заметкой ещё идёт" in completed.stderr
    assert note.read_bytes() == before and not spook.pages()


def test_topic_command_refuses_a_meeting_with_an_existing_topic(spook):
    note = finished_meeting(spook, topic="Raft")
    before = note.read_bytes()

    completed = topic_command(spook, note, TOPIC)

    assert completed.returncode == 1 and f"У Заметки уже есть Тема: {note.name}" in completed.stderr
    assert note.read_bytes() == before and spook.pages() == [note]


def test_topic_command_refuses_missing_and_empty_topic(spook):
    note = finished_meeting(spook)
    before = note.read_bytes()
    missing_note = spook.meetings_dir / "2026-11-06-15-02-vstrecha.html"

    missing = topic_command(spook, missing_note, TOPIC)
    empty = topic_command(spook, note, ' \t ?*"\\ ')

    assert missing.returncode == 1 and f"Заметки нет: {missing_note.name}" in missing.stderr
    assert empty.returncode == 1 and "Тема пуста" in empty.stderr
    assert note.read_bytes() == before and spook.pages() == [note]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([' Raft:\t"репликация" <журнала>/логов? #1 *|\\ '], "Raft репликация журнала логов 1"),
        (
            "Распределённые системы — консенсус, репликация журнала, снимки состояния, перевыборы лидера и членство в кластере".split(),
            "Распределённые системы — консенсус, репликация журнала, снимки состояния, перевы",
        ),
    ],
    ids=["cleaned-topic", "long-topic-as-separate-arguments"],
)
def test_topic_command_cleans_and_limits_topic_preserving_the_address(spook, arguments, expected):
    note = finished_meeting(spook)

    completed = topic_command(spook, note, *arguments)

    assert completed.returncode == 0 and completed.stdout == f"{note}\n"
    assert read_page(note).properties["topic"] == expected
    assert read_page(note).title == f"2026-11-06 14-02 {expected}"


def test_topic_matching_another_page_keeps_both_addresses_and_the_existing_page(spook):
    first = finished_meeting(spook, topic="Raft")
    original = first.read_bytes()
    second = finished_meeting(spook)

    completed = topic_command(spook, second, "Raft")

    assert completed.returncode == 0 and completed.stdout == f"{second}\n"
    assert set(spook.pages()) == {first, second} and first.read_bytes() == original
    assert read_page(second).properties["topic"] == "Raft"


def test_topic_command_refuses_old_markdown_foreign_page_external_path_and_symlink(spook, tmp_path):
    external = tmp_path / "outside.html"
    external.write_text("<h1>Внешняя страница</h1>", encoding="utf-8")
    foreign = spook.meetings_dir / "foreign.html"
    foreign.write_text("<h1>Страница пользователя</h1>", encoding="utf-8")
    old = spook.meetings_dir / "Встреча.md"
    old.write_text("## Транскрипт\n", encoding="utf-8")
    link = spook.meetings_dir / "link.html"
    link.symlink_to(external)

    for path, reason in [
        (external, "Заметка вне Wisp"), (foreign, "Это не Заметка Встречи"),
        (old, "Это не Заметка Встречи"), (link, "Заметки нет"),
    ]:
        before = path.read_bytes()
        completed = topic_command(spook, path, TOPIC)
        assert completed.returncode == 1 and reason in completed.stderr
        assert not completed.stdout and path.read_bytes() == before
    assert link.is_symlink() and not spook.pages()


@pytest.mark.parametrize("edited", [False, True], ids=["original-page", "concurrent-edit"])
def test_setting_topic_can_retry_after_index_failure_and_preserves_concurrent_edits(spook, tmp_path, edited):
    note = finished_meeting(spook)
    previous = read_page(note)
    original = note.read_bytes().replace(b"\n", b"\r\n")
    note.write_bytes(original)
    indexer = spook.wisp_dir / "scripts/wiki_html.py"
    native = indexer.read_bytes()
    expected = original
    script = ""
    if edited:
        expected += "<p>Исправление пользователя.</p>\r\n".encode()
        edit = tmp_path / "edit.html"
        edit.write_bytes(expected)
        script = f"from pathlib import Path\nPath({str(note)!r}).write_bytes(Path({str(edit)!r}).read_bytes())\n"
    indexer.write_text(script + "raise SystemExit(5)\n", encoding="utf-8")
    topic_url = f"{spook.base_url}/agent/topic"
    form = {"note": str(note), "topic": TOPIC}
    try:
        failed = spook.http.post(topic_url, data=form)
    finally:
        indexer.write_bytes(native)

    assert failed.status_code == 409 and "Wisp" in failed.text
    assert note.read_bytes() == expected
    retry = spook.http.post(topic_url, data=form)

    assert retry.status_code == 200 and retry.text == str(note)
    assert spook.pages() == [note]
    assert read_page(note).properties == {**previous.properties, "topic": TOPIC}
    assert read_page(note).transcript == previous.transcript
    [entry] = entries(spook)
    assert entry["file"] == note.name and TOPIC in entry["summary"]
