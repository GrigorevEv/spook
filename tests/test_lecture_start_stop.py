"""Лекция от «Старт» до «Стоп» на подменах — главный шов, HTTP-интерфейс службы."""

from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text, speech


def test_start_of_lecture_creates_note_in_lectures_folder(spook):
    assert not spook.lectures_dir.exists()

    response = spook.start_lecture()

    assert response.status_code == 200, response.text
    assert note_text(spook) == NOTE_AFTER_START


def test_long_speech_is_cut_into_utterances_of_20_seconds_with_time_from_session_start(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.")
    spook.start_lecture()

    spook.audio.feed(speech(50))
    spook.wait_until(lambda: "Вторая реплика." in note_text(spook))

    assert [(heard.start, heard.duration) for heard in spook.recognizer.heard] == [(0.0, 20.0), (20.0, 20.0)]
    assert note_text(spook) == NOTE_AFTER_START + "\n**0:00:00** Первая реплика. Вторая реплика.\n"


def test_silence_is_not_written_to_note(spook):
    spook.recognizer.say("Слова.", "", "Ещё слова.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(60))
        events.wait_for("paragraph", containing="Ещё слова.")

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Слова.\n"
        "\n**0:00:40** Ещё слова.\n"
    )


def test_stop_recognizes_remainder_writes_end_and_returns_to_no_session(spook):
    spook.write_settings(auto_stop_minutes=60)  # Лекция молчит 45 минут до «Стоп»: автостоп не должен успеть
    spook.recognizer.say("Начало.", "Хвост.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(25))
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
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Начало. Хвост.\n"
    )


def test_event_stream_opens_with_full_current_state_and_then_changes(spook):
    spook.recognizer.say("Сказано раньше.", "", "Сказано позже.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Сказано раньше.")

    with spook.events() as events:
        state = events.next_event()
        transcript = events.next_event()
        spook.audio.feed(speech(40))
        later = events.wait_for("paragraph", containing="Сказано позже.")

    assert state.name == "state" and "идёт" in state.data
    assert transcript.name == "transcript" and "Сказано раньше." in transcript.data
    assert "Сказано раньше." not in later.data
