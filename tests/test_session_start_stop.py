from __future__ import annotations

from conftest import NO_SPEECH, speech


def test_start_creates_unpublished_html_note_in_wisp(spook):
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))

    response = spook.start_meeting()

    assert response.status_code == 200, response.text
    assert spook.current_note().parent == spook.drafts_dir and spook.pages() == []
    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == "14:02" and not page.properties.get("end")
    assert page.meta["section"] == "Встречи" and page.meta["tags"] == ["встреча"]
    assert page.transcript == page.summary == ""


def test_long_speech_is_cut_into_utterances_of_20_seconds_with_speaker_and_clock_time(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.")
    spook.start_meeting()

    spook.feed_system(speech(50))
    spook.wait_until(lambda: "Вторая реплика." in spook.note_page().transcript)

    assert [(heard.start, heard.duration) for heard in spook.recognizer.heard] == [(0.0, 20.0), (20.0, 20.0)]
    assert spook.note_page().transcript == "14:02 Собеседники: Первая реплика. Вторая реплика."


def test_silence_is_not_written_to_note(spook):
    spook.recognizer.say("Слова.", NO_SPEECH, "Ещё слова.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(60))
        events.wait_for("paragraph", containing="Ещё слова.")

    assert spook.note_page().transcript == (
        "14:02 Собеседники: Слова.\n\n"
        "14:02 Собеседники: Ещё слова."
    )


def test_stop_recognizes_remainder_writes_end_and_returns_to_no_session(spook):
    spook.write_settings(auto_stop_minutes=60)
    spook.recognizer.say("Начало.", "Хвост.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(25))
        events.wait_for("paragraph", containing="Начало.")
        spook.clock.advance(45 * 60)

        response = spook.stop()

        assert response.status_code == 200, response.text
        events.wait_for("state", containing="завершается")
        events.wait_for("state", containing="нет Сессии")

    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == "14:02"
    assert page.properties["end"] == "14:47"
    assert page.meta["tags"] == ["встреча"]
    assert page.transcript == "14:02 Собеседники: Начало. Хвост."
    assert page.summary == ""


def test_event_stream_opens_with_full_current_state_and_then_changes(spook):
    spook.recognizer.say("Сказано раньше.", NO_SPEECH, "Сказано позже.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Сказано раньше.")

    with spook.events() as events:
        state = events.next_event()
        transcript = events.next_event()
        spook.feed_system(speech(40))
        later = events.wait_for("paragraph", containing="Сказано позже.")

    assert state.name == "state" and "идёт" in state.data
    assert transcript.name == "transcript" and "Сказано раньше." in transcript.data
    assert "Сказано раньше." not in later.data
