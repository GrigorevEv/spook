from __future__ import annotations

from conftest import speech


def test_tail_recognized_after_resume_is_written_before_pause_mark(spook):
    spook.recognizer.say("Начало.", "Хвост.", "После Паузы.")
    spook.recognizer.hold()
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(25))
        spook.clock.advance(25)
        spook.recognizer.release()
        events.wait_for("paragraph", containing="Начало.")
        spook.pause()
        spook.clock.advance(5 * 60)

        assert spook.resume().status_code == 200
        events.wait_for("state", containing="идёт")
        tail_still_recognized = spook.note_page().transcript
        spook.feed_system(speech(20))
        spook.recognizer.release_all()
        events.wait_for("paragraph-update", containing="Хвост.")
        events.wait_for("mark", containing="Пауза")
        events.wait_for("paragraph", containing="После Паузы.")

    assert tail_still_recognized == "14:02 Собеседники: Начало."
    assert spook.note_page().transcript == (
        "14:02 Собеседники: Начало. Хвост.\n\n"
        "Пауза 14:02–14:07\n\n"
        "14:07 Собеседники: После Паузы."
    )
