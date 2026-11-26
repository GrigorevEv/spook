import pytest

from conftest import speech
from test_agent_launch import journal, run_agent
from wisp_page import read_page


def test_successful_summary_preserves_transcript_and_properties_without_warning(spook):
    agent = run_agent(spook)

    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == page.properties["end"] == "14:02"
    assert page.transcript == "14:02 Собеседники: Реплика."
    assert page.summary == "Суть\n\nПро тесты."
    assert 'class="warning"' not in agent.data
    assert "Охрана Транскрипта" not in journal(spook)


@pytest.mark.parametrize("damage", ["transcript", "section", "note"])
def test_spook_restores_damaged_published_transcript_before_applying_reply(spook, damage):
    spook.agent.play("wait")
    spook.recognizer.say("Реплика.")
    assert spook.start_meeting().status_code == 200
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        spook.wait_until(lambda: spook.agent.calls())
        [note] = spook.pages()
        before = read_page(note)
        original = note.read_text(encoding="utf-8")
        if damage == "transcript":
            note.write_text(original.replace("Реплика.", "Другая Реплика."), encoding="utf-8")
        elif damage == "section":
            note.write_text(original.replace('id="транскрипт"', 'id="другое"'), encoding="utf-8")
        else:
            note.unlink()
        spook.agent.release()
        finished = events.wait_for("agent", containing="готово")

    page = read_page(note)
    assert page.transcript == before.transcript
    assert page.properties == before.properties
    assert page.summary == "Суть\n\nПро тесты."
    warning = "Агент изменил Транскрипт — возвращён исходный."
    assert warning in finished.data and warning in spook.page().text
    assert f"Охрана Транскрипта: {warning}" in journal(spook)
