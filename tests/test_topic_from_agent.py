"""Тема от Агента: команда `spook topic <путь к Заметке> <Тема>` — та, которой Скилл сообщает придуманную
Тему. Тесты вызывают её так, как Скилл: отдельным процессом из папки вида, в чистом окружении
(только HOME, PATH и расположение настроек), и через заглушку Агента."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import speech

NOTE_WITHOUT_TOPIC = "2026-11-06 14-02 Лекция.md"


def note_names(spook) -> list[str]:
    return sorted(path.name for path in spook.lectures_dir.iterdir())


def finished_lecture(spook, topic: str = "") -> Path:
    """Лекция с Репликой, завершённая по «Стоп»; заглушка Агента (сценарий по умолчанию) уже отработала
    без Выжимки. Возвращает путь к Заметке."""
    spook.recognizer.say("Лидер рассылает записи журнала.")
    assert spook.start_lecture(topic=topic).status_code == 200
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        events.wait_for("agent", containing="ошибка")
    return spook.lectures_dir / f"2026-11-06 14-02 {topic or 'Лекция'}.md"


def topic_command(spook, note: Path, *topic: str, settings: Path | None = None) -> subprocess.CompletedProcess:
    """`spook topic <путь> <Тема>`, как её вызывает Скилл: из папки, где лежит Заметка, без переменных
    окружения сессии — только HOME, PATH и расположение настроек (`XDG_CONFIG_HOME` временного каталога)."""
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
    note = finished_lecture(spook)
    renamed = spook.lectures_dir / "2026-11-06 14-02 Репликация в Raft.md"

    with spook.events() as events:
        events.next_event()
        completed = topic_command(spook, note, "Репликация в Raft")
        agent = events.wait_for("agent", containing="2026-11-06 14-02 Репликация в Raft")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{renamed}\n"
    assert completed.stderr == ""
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert "2026-11-06 14-02 Лекция" not in agent.data
    page = spook.page().text
    assert str(renamed) in page
    assert "2026-11-06 14-02 Лекция" not in page


def test_agent_that_reports_topic_and_writes_summary_at_new_path_gives_done_under_new_name(spook):
    spook.agent.play("topic")  # заглушка сообщает Тему «Репликация в Raft» командой, затем пишет Выжимку
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    new_name = "2026-11-06 14-02 Репликация в Raft"
    assert new_name in agent.data and "ошибка" not in agent.data
    assert note_names(spook) == [f"{new_name}.md"]
    assert "## О чём" in (spook.lectures_dir / f"{new_name}.md").read_text(encoding="utf-8")
    # Агент получил прежний путь, а Выжимку дописал по новому; журнал и уведомление — под новым именем.
    assert [call["argv"][1] for call in spook.agent.calls()] == [f"/spook-lecture {spook.lectures_dir / NOTE_WITHOUT_TOPIC}"]
    assert [path.name for path in (spook.state_dir / "agent" / "lecture").iterdir()] == [f"{new_name}.log"]
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": new_name})
    assert journal.status_code == 200
    assert "Выжимка: готово" in journal.text
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", new_name]]


def test_transcript_guard_follows_note_renamed_by_agent_and_does_not_restore_it_under_old_name(spook):
    spook.agent.play("topic")
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    new_name = "2026-11-06 14-02 Репликация в Raft"
    assert note_names(spook) == [f"{new_name}.md"]
    assert (spook.lectures_dir / f"{new_name}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:02"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Лидер рассылает записи журнала.\n"
    )
    assert 'class="warning"' not in agent.data
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": new_name}).text
    assert "Охрана Транскрипта" not in journal


def test_topic_command_is_rejected_while_session_with_this_note_is_running(spook):
    spook.start_lecture()
    note = spook.lectures_dir / NOTE_WITHOUT_TOPIC

    completed = topic_command(spook, note, "Репликация в Raft")

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "Сессия с этой Заметкой ещё идёт" in completed.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]


def test_topic_command_is_rejected_for_note_that_already_has_topic(spook):
    note = finished_lecture(spook, topic="Raft")

    completed = topic_command(spook, note, "Репликация в Raft")

    assert completed.returncode == 1
    assert "У Заметки уже есть Тема: 2026-11-06 14-02 Raft.md" in completed.stderr
    assert note_names(spook) == ["2026-11-06 14-02 Raft.md"]


def test_topic_command_is_rejected_for_note_outside_kind_folders(spook, tmp_path: Path):
    outside = tmp_path / "notes" / NOTE_WITHOUT_TOPIC
    outside.parent.mkdir()
    outside.write_text("## Транскрипт\n", encoding="utf-8")

    completed = topic_command(spook, outside, "Репликация в Raft")

    assert completed.returncode == 1
    assert f"Заметка вне папок Встреч и Лекций: {outside}" in completed.stderr
    assert [path.name for path in outside.parent.iterdir()] == [NOTE_WITHOUT_TOPIC]


def test_topic_command_is_rejected_for_missing_note_and_for_empty_topic(spook):
    note = finished_lecture(spook)

    missing = topic_command(spook, spook.lectures_dir / "2026-11-06 15-02 Лекция.md", "Репликация в Raft")
    empty = topic_command(spook, note, ' \t ?*"\\ ')

    assert missing.returncode == 1
    assert "Заметки нет: " in missing.stderr and "2026-11-06 15-02 Лекция.md" in missing.stderr
    assert empty.returncode == 1
    assert "Тема пуста" in empty.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]


def test_topic_from_agent_is_cleaned_of_forbidden_characters_like_user_input(spook):
    note = finished_lecture(spook)

    completed = topic_command(spook, note, ' Raft:\t"репликация" <журнала>/логов? #1 *|\\ ')

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{spook.lectures_dir / '2026-11-06 14-02 Raft репликация журнала логов 1.md'}\n"
    assert note_names(spook) == ["2026-11-06 14-02 Raft репликация журнала логов 1.md"]


def test_long_topic_from_agent_given_as_separate_words_is_cut_to_80_characters(spook):
    note = finished_lecture(spook)

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
    finished_lecture(spook, topic="Raft")
    note = finished_lecture(spook)

    completed = topic_command(spook, note, "Raft")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{spook.lectures_dir / '2026-11-06 14-02 Raft (2).md'}\n"
    assert note_names(spook) == ["2026-11-06 14-02 Raft (2).md", "2026-11-06 14-02 Raft.md"]


def test_topic_command_is_rejected_for_link_in_kind_folder_that_leads_outside(spook, tmp_path: Path):
    # Та же проверка пути, что у команд экрана «Заметки»: файл прямо в папке вида, без ссылок наружу.
    outside = tmp_path / "outside.md"
    outside.write_text("## Транскрипт\n", encoding="utf-8")
    spook.lectures_dir.mkdir(parents=True)
    link = spook.lectures_dir / NOTE_WITHOUT_TOPIC
    link.symlink_to(outside)

    completed = topic_command(spook, link, "Репликация в Raft")

    assert completed.returncode == 1
    assert f"Заметки нет: {NOTE_WITHOUT_TOPIC}" in completed.stderr
    assert note_names(spook) == [NOTE_WITHOUT_TOPIC]
