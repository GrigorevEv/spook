"""Охрана Транскрипта на заглушке Агента: что бы Агент ни сделал от «## Транскрипт» и ниже,
после его работы Транскрипт прежний, Выжимка на месте, а о вмешательстве сказано на странице и в журнале."""

from __future__ import annotations

from conftest import SseEvent, speech

NOTE_NAME = "2026-11-06 14-02 Лекция"

# Заметка Лекции с одной Репликой после честного Агента (сценарий заглушки `summary`).
NOTE_WITH_SUMMARY = (
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
    "\n**0:00:00** Реплика.\n"
)

# Та же Заметка до работы Агента: Выжимки ещё нет.
NOTE_WITHOUT_SUMMARY = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    'end: "14:02"\n'
    "tags: [лекция]\n"
    "---\n"
    "\n"
    "## Транскрипт\n"
    "\n**0:00:00** Реплика.\n"
)


def run_agent(spook, scenario: str, until: str) -> SseEvent:
    """Лекция с одной Репликой, «Стоп», Агент по сценарию; возвращает событие `agent` с итогом `until`."""
    spook.agent.play(scenario)
    spook.recognizer.say("Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        return events.wait_for("agent", containing=until)


def note_text(spook) -> str:
    return (spook.lectures_dir / f"{NOTE_NAME}.md").read_text(encoding="utf-8")


def journal(spook) -> str:
    return (spook.state_dir / "agent" / "lecture" / f"{NOTE_NAME}.log").read_text(encoding="utf-8")


def test_agent_that_left_transcript_alone_causes_no_notice(spook):
    agent = run_agent(spook, "summary", until="готово")

    assert note_text(spook) == NOTE_WITH_SUMMARY
    assert "Транскрипт" not in agent.data
    assert "Транскрипт" not in spook.page().text
    assert "Охрана Транскрипта" not in journal(spook)


def test_transcript_rewritten_by_agent_is_restored_with_summary_kept_and_page_and_journal_say_so(spook):
    agent = run_agent(spook, "rewrite-transcript", until="готово")

    assert note_text(spook) == NOTE_WITH_SUMMARY
    assert "Агент изменил Транскрипт — возвращён исходный." in agent.data
    assert "Агент изменил Транскрипт — возвращён исходный." in spook.page().text
    assert journal(spook).endswith(
        "Заглушка Агента: сценарий «rewrite-transcript».\n"
        "Заглушка Агента: Выжимка написана.\n"
        "Заглушка Агента: Транскрипт переписан.\n"
        "\n"
        "Охрана Транскрипта: Агент изменил Транскрипт — возвращён исходный.\n"
        "\n"
        "Код возврата: 0. Выжимка: готово\n"
    )


def test_transcript_heading_erased_by_agent_comes_back_with_transcript_under_it(spook):
    agent = run_agent(spook, "erase-heading", until="готово")

    assert note_text(spook) == NOTE_WITH_SUMMARY
    assert "Агент стёр заголовок «Транскрипт» — Транскрипт возвращён на место." in agent.data
    assert "Охрана Транскрипта: Агент стёр заголовок «Транскрипт» — Транскрипт возвращён на место." in journal(spook)


def test_note_deleted_by_agent_is_written_back_with_properties_and_transcript_and_counts_as_error(spook):
    agent = run_agent(spook, "delete-note", until="ошибка")

    assert note_text(spook) == NOTE_WITHOUT_SUMMARY
    assert "Выжимки в Заметке нет" in agent.data
    assert "Агент стёр Заметку — восстановлены свойства и Транскрипт." in agent.data
    assert "Охрана Транскрипта: Агент стёр Заметку — восстановлены свойства и Транскрипт." in journal(spook)
