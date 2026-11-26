from __future__ import annotations

from spook.audio import Source

from conftest import NO_SPEECH, speech

NOTE_NAME = "2026-11-06 14-02 Встреча"


def test_silence_longer_than_auto_stop_finishes_session_like_stop_and_notifies(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Единственная Реплика.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Единственная Реплика.")
        spook.clock.advance(5 * 60 - 1)
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="завершается")
        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")

    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == "14:02"
    assert page.properties["end"] == "14:07"
    assert page.meta["tags"] == ["встреча"]
    assert page.transcript == "14:02 Собеседники: Единственная Реплика."
    assert "Про тесты." in page.summary
    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "5 минут без речи"],
        ["Выжимка готова", NOTE_NAME],
    ]
    assert ">Старт</button>" in spook.page().text


def test_speech_restarts_countdown_but_segment_without_speech_does_not(spook):
    spook.recognizer.say("Сказано на четвёртой минуте.", NO_SPEECH)
    spook.start_meeting()

    with spook.events() as events:
        spook.clock.advance(4 * 60)
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Сказано на четвёртой минуте.")
        spook.clock.advance(4 * 60)
        assert "идёт" in spook.page().text
        spook.feed_system(speech(20))
        spook.wait_until(lambda: len(spook.recognizer.heard) == 2)
        assert "идёт" in spook.page().text

        spook.clock.advance(60)

        events.wait_for("state", containing="нет Сессии")

    assert spook.note_page().properties["end"] == "14:11"


def test_speech_in_microphone_alone_restarts_countdown_in_meeting(spook):
    spook.recognizer.say("Я говорю на четвёртой минуте.", NO_SPEECH)
    spook.start_meeting()

    with spook.events() as events:
        spook.clock.advance(4 * 60)
        spook.audio.feed(speech(20), Source.MIC)
        spook.wait_until(lambda: len(spook.recognizer.heard) == 1)
        spook.audio.feed(speech(20), Source.SYSTEM)
        spook.wait_until(lambda: "Я говорю на четвёртой минуте." in spook.note_page().transcript)
        spook.clock.advance(4 * 60)
        assert "идёт" in spook.page().text

        spook.clock.advance(60)

        events.wait_for("state", containing="нет Сессии")

    assert spook.note_page().properties["end"] == "14:11"


def test_pause_time_does_not_count_and_countdown_restarts_after_resume(spook):
    spook.recognizer.say("До Паузы.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="До Паузы.")
        spook.clock.advance(3 * 60)
        spook.pause()
        spook.clock.advance(60 * 60)
        assert "на Паузе" in spook.page().text

        assert spook.resume().status_code == 200
        spook.clock.advance(5 * 60 - 1)
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="нет Сессии")

    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == "14:02"
    assert page.properties["end"] == "15:10"
    assert page.meta["tags"] == ["встреча"]
    assert page.transcript == (
        "14:02 Собеседники: До Паузы.\n\n"
        "Пауза 14:05–15:05"
    )
    assert page.summary == ""


def test_auto_stop_time_comes_from_settings_and_counts_from_start_without_speech(spook):
    spook.write_settings(auto_stop_minutes=1.5)
    spook.start_meeting()

    with spook.events() as events:
        spook.clock.advance(89)
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="нет Сессии")

    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Сессия завершена", "90 секунд без речи"]]
    assert spook.agent.calls() == [] and spook.pages() == []
