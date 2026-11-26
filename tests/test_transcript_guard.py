from __future__ import annotations

import pytest

from conftest import note_text, speech
from test_agent_launch import journal, run_agent

NOTE_NAME = "2026-11-06 14-02 Встреча"

NOTE_WITH_SUMMARY = (
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
    "\n**14:02 Собеседники:** Реплика.\n"
)

NOTE_WITHOUT_SUMMARY = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    'end: "14:02"\n'
    "tags: [встреча]\n"
    "---\n"
    "\n"
    "## Транскрипт\n"
    "\n**14:02 Собеседники:** Реплика.\n"
)


def test_successful_summary_preserves_transcript_and_properties_without_warning(spook):
    agent = run_agent(spook)
    assert note_text(spook) == NOTE_WITH_SUMMARY
    assert 'class="warning"' not in agent.data
    assert "Охрана Транскрипта" not in journal(spook)


@pytest.mark.parametrize(
    ("damage", "warning"),
    [
        ("transcript", "Агент изменил Транскрипт — возвращён исходный."),
        ("heading", "Агент стёр заголовок «Транскрипт» — Транскрипт возвращён на место."),
        ("note", "Агент стёр Заметку — восстановлены свойства и Транскрипт."),
    ],
)
def test_spook_restores_damaged_note_before_applying_reply(spook, damage, warning):
    spook.agent.play("wait")
    spook.recognizer.say("Реплика.")
    assert spook.start_meeting().status_code == 200
    note = spook.meetings_dir / f"{NOTE_NAME}.md"
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        spook.wait_until(lambda: spook.agent.calls())
        before = note.read_text(encoding="utf-8")
        assert before == NOTE_WITHOUT_SUMMARY
        if damage == "transcript":
            note.write_text(before.replace("Реплика.", "Другая Реплика."), encoding="utf-8")
        elif damage == "heading":
            note.write_text(before.replace("## Транскрипт\n", ""), encoding="utf-8")
        else:
            note.unlink()
        spook.agent.release()
        finished = events.wait_for("agent", containing="готово")

    assert note.read_text(encoding="utf-8") == NOTE_WITH_SUMMARY
    assert warning in finished.data
    assert warning in spook.page().text
    assert f"Охрана Транскрипта: {warning}" in journal(spook)
