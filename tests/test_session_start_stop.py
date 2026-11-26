from __future__ import annotations

from conftest import NO_SPEECH, NOTE_AFTER_START, note_text, speech


def test_start_creates_note_in_meetings_folder(spook):
    assert not spook.meetings_dir.exists()

    response = spook.start_meeting()

    assert response.status_code == 200, response.text
    assert note_text(spook) == NOTE_AFTER_START


def test_long_speech_is_cut_into_utterances_of_20_seconds_with_speaker_and_clock_time(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.")
    spook.start_meeting()

    spook.feed_system(speech(50))
    spook.wait_until(lambda: "Вторая реплика." in note_text(spook))

    assert [(heard.start, heard.duration) for heard in spook.recognizer.heard] == [(0.0, 20.0), (20.0, 20.0)]
    assert note_text(spook) == NOTE_AFTER_START + "\n**14:02 Собеседники:** Первая реплика. Вторая реплика.\n"


def test_silence_is_not_written_to_note(spook):
    spook.recognizer.say("Слова.", NO_SPEECH, "Ещё слова.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(60))
        events.wait_for("paragraph", containing="Ещё слова.")

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Слова.\n"
        "\n**14:02 Собеседники:** Ещё слова.\n"
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

    assert note_text(spook) == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:47"\n'
        "tags: [встреча]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Начало. Хвост.\n"
    )


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
