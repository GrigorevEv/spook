from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text, speech


def test_tail_recognized_after_resume_is_written_before_pause_mark(spook):
    spook.recognizer.say("Начало.", "Хвост.", "После Паузы.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(25))
        spook.clock.advance(25)
        spook.recognizer.release()
        events.wait_for("paragraph", containing="Начало.")
        spook.pause()
        spook.clock.advance(5 * 60)

        assert spook.resume().status_code == 200
        events.wait_for("state", containing="идёт")
        tail_still_recognized = note_text(spook)
        spook.audio.feed(speech(20))
        spook.recognizer.release_all()
        events.wait_for("paragraph-update", containing="Хвост.")
        events.wait_for("mark", containing="Пауза")
        events.wait_for("paragraph", containing="После Паузы.")

    assert tail_still_recognized == NOTE_AFTER_START + "\n**0:00:00** Начало.\n"
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Начало. Хвост.\n"
        "\n*Пауза 0:00:25–0:05:25*\n"
        "\n**0:00:25** После Паузы.\n"
    )
