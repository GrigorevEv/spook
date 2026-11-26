from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import speech

NOTE_WITHOUT_TOPIC = "2026-11-06 14-02 Встреча.md"


def note_names(spook) -> list[str]:
    return sorted(path.name for path in spook.meetings_dir.iterdir())


def finished_meeting(spook, topic: str = "") -> Path:
    spook.agent.play("fail")
    spook.recognizer.say("Лидер рассылает записи журнала.")
    assert spook.start_meeting(topic=topic).status_code == 200
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        events.wait_for("agent", containing="ошибка")
    return spook.meetings_dir / f"2026-11-06 14-02 {topic or 'Встреча'}.md"


def topic_command(spook, note: Path, *topic: str, settings: Path | None = None) -> subprocess.CompletedProcess:
    environment = {
        "HOME": os.environ["HOME"],
        "PATH": os.environ.get("PATH", ""),
        "XDG_CONFIG_HOME": str(spook.settings_path.parents[1]),
    }
    options = [] if settings is None else ["--settings", str(settings)]
    return subprocess.run(
        [sys.executable, "-m", "spook.cli", "topic", *options, str(note), *topic],
        cwd=note.parent,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_topic_command_renames_finished_note_without_topic_prints_new_path_and_page_shows_new_name(spook):
    note = finished_meeting(spook)
    renamed = spook.meetings_dir / "2026-11-06 14-02 Репликация в Raft.md"

    with spook.events() as events:
        events.next_event()
        completed = topic_command(spook, note, "Репликация в Raft")
        agent = events.wait_for("agent", containing="2026-11-06 14-02 Репликация в Raft")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{renamed}\n"
    assert completed.stderr == ""
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert "2026-11-06 14-02 Встреча" not in agent.data
    page = spook.page().text
    assert str(renamed) in page
    assert "2026-11-06 14-02 Встреча" not in page


def test_spook_saves_agent_reply_and_reports_done_under_new_topic(spook):
    spook.agent.play("topic")
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    new_name = "2026-11-06 14-02 Репликация в Raft"
    assert new_name in agent.data and "ошибка" not in agent.data
    assert note_names(spook) == [f"{new_name}.md"]
    assert "## Суть" in (spook.meetings_dir / f"{new_name}.md").read_text(encoding="utf-8")
    [call] = spook.agent.calls()
    assert call["input"]["topic"] == ""
    assert call["input"]["transcript"].strip() == "**14:02 Собеседники:** Лидер рассылает записи журнала."
    assert [path.name for path in (spook.state_dir / "agent" / "meeting").iterdir()] == [f"{new_name}.log"]
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": new_name})
    assert journal.status_code == 200
    assert "Выжимка: готово" in journal.text
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", new_name]]


def test_transcript_guard_follows_note_renamed_by_agent_and_does_not_restore_it_under_old_name(spook):
    spook.agent.play("topic")
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    new_name = "2026-11-06 14-02 Репликация в Raft"
    assert note_names(spook) == [f"{new_name}.md"]
    assert (spook.meetings_dir / f"{new_name}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:02"\n'
        "tags: [встреча]\n"
        "---\n"
        "\n"
        "## Суть\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Лидер рассылает записи журнала.\n"
    )
    assert 'class="warning"' not in agent.data
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": new_name}).text
    assert "Охрана Транскрипта" not in journal


def test_topic_command_is_rejected_while_session_with_this_note_is_running(spook):
    spook.start_meeting()
    note = spook.meetings_dir / NOTE_WITHOUT_TOPIC

    completed = topic_command(spook, note, "Репликация в Raft")

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "Сессия с этой Заметкой ещё идёт" in completed.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]


def test_topic_command_is_rejected_for_note_that_already_has_topic(spook):
    note = finished_meeting(spook, topic="Raft")

    completed = topic_command(spook, note, "Репликация в Raft")

    assert completed.returncode == 1
    assert "У Заметки уже есть Тема: 2026-11-06 14-02 Raft.md" in completed.stderr
    assert note_names(spook) == ["2026-11-06 14-02 Raft.md"]


def test_topic_command_is_rejected_for_note_outside_meetings_folder(spook, tmp_path: Path):
    outside = tmp_path / "notes" / NOTE_WITHOUT_TOPIC
    outside.parent.mkdir()
    outside.write_text("## Транскрипт\n", encoding="utf-8")

    completed = topic_command(spook, outside, "Репликация в Raft")

    assert completed.returncode == 1
    assert f"Заметка вне папки Встреч: {outside}" in completed.stderr
    assert [path.name for path in outside.parent.iterdir()] == [NOTE_WITHOUT_TOPIC]


def test_topic_command_is_rejected_for_missing_note_and_for_empty_topic(spook):
    note = finished_meeting(spook)

    missing = topic_command(spook, spook.meetings_dir / "2026-11-06 15-02 Встреча.md", "Репликация в Raft")
    empty = topic_command(spook, note, ' \t ?*"\\ ')

    assert missing.returncode == 1
    assert "Заметки нет: " in missing.stderr and "2026-11-06 15-02 Встреча.md" in missing.stderr
    assert empty.returncode == 1
    assert "Тема пуста" in empty.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]


def test_topic_from_agent_is_cleaned_of_forbidden_characters_like_user_input(spook):
    note = finished_meeting(spook)

    completed = topic_command(spook, note, ' Raft:\t"репликация" <журнала>/логов? #1 *|\\ ')

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{spook.meetings_dir / '2026-11-06 14-02 Raft репликация журнала логов 1.md'}\n"
    assert note_names(spook) == ["2026-11-06 14-02 Raft репликация журнала логов 1.md"]


def test_long_topic_from_agent_given_as_separate_words_is_cut_to_80_characters(spook):
    note = finished_meeting(spook)

    completed = topic_command(
        spook,
        note,
        *"Распределённые системы — консенсус, репликация журнала, снимки состояния, "
        "перевыборы лидера и членство в кластере".split(" "),
    )

    assert completed.returncode == 0, completed.stderr
    assert note_names(spook) == [
        "2026-11-06 14-02 Распределённые системы — консенсус, репликация журнала, снимки состояния, перевы.md"
    ]


def test_topic_that_repeats_name_of_another_note_gets_number_two(spook):
    finished_meeting(spook, topic="Raft")
    note = finished_meeting(spook)

    completed = topic_command(spook, note, "Raft")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{spook.meetings_dir / '2026-11-06 14-02 Raft (2).md'}\n"
    assert note_names(spook) == ["2026-11-06 14-02 Raft (2).md", "2026-11-06 14-02 Raft.md"]


def test_topic_command_is_rejected_for_link_in_meetings_folder_that_leads_outside(spook, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("## Транскрипт\n", encoding="utf-8")
    spook.meetings_dir.mkdir(parents=True)
    link = spook.meetings_dir / NOTE_WITHOUT_TOPIC
    link.symlink_to(outside)

    completed = topic_command(spook, link, "Репликация в Raft")

    assert completed.returncode == 1
    assert f"Заметки нет: {NOTE_WITHOUT_TOPIC}" in completed.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]
