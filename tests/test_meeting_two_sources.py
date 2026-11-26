from __future__ import annotations

import json

import numpy as np

from spook.audio import Source

from conftest import NO_SPEECH, SAMPLE_RATE, speech


def test_explicit_meeting_from_previous_client_creates_meeting_note(spook):
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))

    response = spook.http.post(f"{spook.base_url}/session/start", data={"kind": "meeting"})

    assert response.status_code == 200, response.text
    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}
    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06" and page.properties["start"] == "14:02"
    assert page.meta["section"] == "Встречи" and page.meta["tags"] == ["встреча"]
    assert page.transcript == ""


def test_utterances_of_both_sources_get_speaker_and_clock_time(spook):
    spook.recognizer.say("Коллеги, всем привет, начинаем. Сегодня два вопроса…", NO_SPEECH, NO_SPEECH, NO_SPEECH)
    spook.recognizer.say(NO_SPEECH, NO_SPEECH, NO_SPEECH, "Привет. По первому: вчера выкатили…")
    spook.start_meeting()
    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}

    with spook.events() as events:
        spook.audio.feed(speech(80), Source.SYSTEM)
        spook.audio.feed(speech(80), Source.MIC)
        them = events.wait_for("paragraph", containing="Коллеги, всем привет")
        me = events.wait_for("paragraph", containing="Привет. По первому")

    assert spook.note_page().transcript == (
        "14:02 Собеседники: Коллеги, всем привет, начинаем. Сегодня два вопроса…\n\n"
        "14:03 Я: Привет. По первому: вчера выкатили…"
    )
    assert "<time>14:02</time>" in them.data and "Собеседники" in them.data
    assert "<time>14:03</time>" in me.data and ">Я<" in me.data


def test_change_of_speaker_starts_new_paragraph_even_without_silence(spook):
    spook.recognizer.say("Коллеги, всем привет, начинаем.", "Сегодня два вопроса.", "Первый — релиз.")
    spook.recognizer.say(NO_SPEECH, NO_SPEECH, NO_SPEECH, "Привет. По первому: вчера выкатили.")
    spook.recognizer.say(NO_SPEECH, "Хорошо, продолжаем.")
    spook.start_meeting()

    spook.audio.feed(speech(60), Source.SYSTEM)
    spook.audio.feed(speech(80), Source.MIC)
    spook.audio.feed(speech(40), Source.SYSTEM)
    spook.wait_until(lambda: "Хорошо, продолжаем." in spook.note_page().transcript)

    assert spook.note_page().transcript == (
        "14:02 Собеседники: Коллеги, всем привет, начинаем. Сегодня два вопроса. Первый — релиз.\n\n"
        "14:03 Я: Привет. По первому: вчера выкатили.\n\n"
        "14:03 Собеседники: Хорошо, продолжаем."
    )


def test_later_mic_utterance_is_written_after_earlier_system_utterance_recognized_later(spook):
    spook.recognizer.say(NO_SPEECH, "Привет.")
    spook.recognizer.say("Коллеги, всем привет.")
    spook.start_meeting()

    spook.audio.feed(speech(40), Source.MIC)
    spook.wait_until(lambda: len(spook.recognizer.heard) == 2)
    while_system_sound_pending = spook.note_page().transcript
    spook.audio.feed(speech(20), Source.SYSTEM)
    spook.wait_until(lambda: "Привет." in spook.note_page().transcript)

    assert [(heard.source, heard.start) for heard in spook.recognizer.heard] == [
        (Source.MIC, 0.0),
        (Source.MIC, 20.0),
        (Source.SYSTEM, 0.0),
    ]
    assert while_system_sound_pending == ""
    assert spook.note_page().transcript == (
        "14:02 Собеседники: Коллеги, всем привет.\n\n"
        "14:02 Я: Привет."
    )


def test_start_defaults_to_meeting_and_rejects_the_old_lecture_request(spook):
    page = spook.page().text
    assert 'name="kind"' not in page and 'name="link"' not in page
    assert "Лекция" not in page
    old = spook.http.post(f"{spook.base_url}/session/start", data={"kind": "lecture"})
    assert old.status_code == 409 and spook.audio.active_sources == set()
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))

    assert spook.http.post(f"{spook.base_url}/session/start").status_code == 200

    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}
    assert "Встреча" in spook.page().text


def test_pause_in_meeting_is_marked_by_clock_and_time_after_pause_goes_by_clock_too(spook):
    spook.recognizer.say("До Паузы.", NO_SPEECH)
    spook.recognizer.say(NO_SPEECH, "После Паузы.")
    spook.start_meeting()

    with spook.events() as events:
        spook.audio.feed(speech(20), Source.SYSTEM)
        spook.audio.feed(speech(20), Source.MIC)
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До Паузы.")
        spook.pause()
        spook.clock.advance(5 * 60)
        spook.resume()
        mark = events.wait_for("mark", containing="Пауза")
        spook.audio.feed(speech(20), Source.SYSTEM)
        spook.audio.feed(speech(20), Source.MIC)
        after = events.wait_for("paragraph", containing="После Паузы.")

    assert "<em>Пауза 14:02–14:07</em>" in mark.data
    assert "<time>14:07</time>" in after.data
    assert spook.note_page().transcript == (
        "14:02 Собеседники: До Паузы.\n\n"
        "Пауза 14:02–14:07\n\n"
        "14:07 Я: После Паузы."
    )


def test_meeting_state_names_both_sources_and_levels_come_for_each(spook):
    spook.start_meeting()

    with spook.events() as events:
        state = events.next_event()
        spook.audio.feed(np.zeros(SAMPLE_RATE // 10, dtype=np.float32), Source.MIC)
        levels = events.wait_for("levels")

    assert 'data-source="system"' in state.data and "Системный звук" in state.data
    assert 'data-source="mic"' in state.data and "Микрофон" in state.data
    assert json.loads(levels.data) == {"mic": {"rms": 0.0, "peak": 0.0}}


def test_start_of_meeting_without_microphone_is_rejected_with_reason_and_leaves_nothing_behind(spook):
    spook.audio.unavailable.add(Source.MIC)

    response = spook.start_meeting()

    assert response.status_code == 409
    assert "Микрофон" in response.text
    assert "нет Сессии" in spook.page().text
    assert spook.pages() == []
    assert spook.audio.active_sources == set()
    spook.audio.unavailable.clear()
    assert spook.start_meeting().status_code == 200
