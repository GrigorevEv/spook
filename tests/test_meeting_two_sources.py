"""Встреча: два Источника, пометки «Я» и «Собеседники», время по часам — главный шов: HTTP, файлы Заметок, поток событий.

Подменённый вход режет звук принудительно по `max_utterance_seconds` (20 секунд), поэтому Реплики
каждого Источника идут вплотную от его нуля; тишина создаётся отрезками, которые сценарный
распознаватель считает тишиной. Тексты сценария расходуются в порядке распознавания — в порядке подачи звука.
"""

from __future__ import annotations

import json

import numpy as np

from spook.audio import Source

from conftest import MEETING_NOTE_AFTER_START, NOTE_AFTER_START, SAMPLE_RATE, meeting_note_text, note_text, speech


def test_start_of_meeting_creates_note_in_meetings_folder_with_meeting_tag(spook):
    assert not spook.meetings_dir.exists()

    response = spook.start_meeting()

    assert response.status_code == 200, response.text
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START
    assert not spook.lectures_dir.exists()


def test_utterances_of_both_sources_get_speaker_and_clock_time(spook):
    # Как в примере спецификации: собеседники говорят с начала, пользователь отвечает через минуту.
    spook.recognizer.say("Коллеги, всем привет, начинаем. Сегодня два вопроса…", "", "", "")  # Системный звук, 0–80 с
    spook.recognizer.say("", "", "", "Привет. По первому: вчера выкатили…")  # Микрофон, 0–80 с
    spook.start_meeting()
    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}

    with spook.events() as events:
        spook.audio.feed(speech(80), Source.SYSTEM)
        spook.audio.feed(speech(80), Source.MIC)
        them = events.wait_for("paragraph", containing="Коллеги, всем привет")
        me = events.wait_for("paragraph", containing="Привет. По первому")

    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Коллеги, всем привет, начинаем. Сегодня два вопроса…\n"
        "\n**14:03 Я:** Привет. По первому: вчера выкатили…\n"
    )
    assert "<time>14:02</time>" in them.data and "Собеседники" in them.data
    assert "<time>14:03</time>" in me.data and ">Я<" in me.data


def test_change_of_speaker_starts_new_paragraph_even_without_silence(spook):
    # Реплики идут вплотную: собеседники 0–60 с, пользователь 60–80 с, собеседники снова 80–100 с.
    spook.recognizer.say("Коллеги, всем привет, начинаем.", "Сегодня два вопроса.", "Первый — релиз.")  # Системный звук, 0–60 с
    spook.recognizer.say("", "", "", "Привет. По первому: вчера выкатили.")  # Микрофон, 0–80 с
    spook.recognizer.say("", "Хорошо, продолжаем.")  # Системный звук, 60–100 с
    spook.start_meeting()

    spook.audio.feed(speech(60), Source.SYSTEM)
    spook.audio.feed(speech(80), Source.MIC)
    spook.audio.feed(speech(40), Source.SYSTEM)
    spook.wait_until(lambda: "Хорошо, продолжаем." in meeting_note_text(spook))

    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Коллеги, всем привет, начинаем. Сегодня два вопроса. Первый — релиз.\n"
        "\n**14:03 Я:** Привет. По первому: вчера выкатили.\n"
        "\n**14:03 Собеседники:** Хорошо, продолжаем.\n"
    )


def test_later_mic_utterance_is_written_after_earlier_system_utterance_recognized_later(spook):
    spook.recognizer.say("", "Привет.")  # Микрофон, 0–40 с: Реплика начинается на 20-й секунде
    spook.recognizer.say("Коллеги, всем привет.")  # Системный звук, 0–20 с: начинается раньше, распознаётся позже
    spook.start_meeting()

    spook.audio.feed(speech(40), Source.MIC)
    spook.wait_until(lambda: len(spook.recognizer.heard) == 2)
    while_system_sound_pending = meeting_note_text(spook)
    spook.audio.feed(speech(20), Source.SYSTEM)
    spook.wait_until(lambda: "Привет." in meeting_note_text(spook))

    assert [(heard.source, heard.start) for heard in spook.recognizer.heard] == [
        (Source.MIC, 0.0),
        (Source.MIC, 20.0),
        (Source.SYSTEM, 0.0),
    ]
    # Реплика Микрофона придержана: Системный звук ещё мог дать Реплику, начавшуюся раньше.
    assert while_system_sound_pending == MEETING_NOTE_AFTER_START
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Коллеги, всем привет.\n"
        "\n**14:02 Я:** Привет.\n"
    )


def test_page_offers_kind_choice_before_start_and_kind_does_not_change_after_start(spook):
    before = spook.page().text
    assert 'type="radio" name="kind" value="meeting"' in before and "Встреча" in before
    assert 'type="radio" name="kind" value="lecture"' in before and "Лекция" in before

    assert spook.start_meeting().status_code == 200
    response = spook.start_lecture()

    assert response.status_code == 409 and "Сессия уже идёт" in response.text
    during = spook.page().text
    assert 'name="kind"' not in during  # вид после «Старт» не меняется: выбора на странице нет
    assert '<span class="kind">Встреча</span>' in during
    assert [path.name for path in spook.meetings_dir.iterdir()] == ["2026-11-06 14-02 Встреча.md"]
    assert not spook.lectures_dir.exists()


def test_pause_in_meeting_is_marked_by_clock_and_time_after_pause_goes_by_clock_too(spook):
    spook.recognizer.say("До Паузы.", "")  # Системный звук и Микрофон до Паузы, по 20 с
    spook.recognizer.say("", "После Паузы.")  # Системный звук и Микрофон после Паузы, по 20 с
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
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** До Паузы.\n"
        "\n*Пауза 14:02–14:07*\n"
        "\n**14:07 Я:** После Паузы.\n"
    )


def test_lecture_still_captures_only_system_sound_and_marks_no_speaker(spook):
    spook.recognizer.say("Слова лектора.")
    spook.start_lecture()
    assert spook.audio.active_sources == {Source.SYSTEM}

    spook.audio.feed(speech(20), Source.MIC)  # Микрофон в Лекции не захватывается: звук пропадает
    spook.audio.feed(speech(20), Source.SYSTEM)
    spook.wait_until(lambda: "Слова лектора." in note_text(spook))

    assert [(heard.source, heard.start) for heard in spook.recognizer.heard] == [(Source.SYSTEM, 0.0)]
    assert note_text(spook) == NOTE_AFTER_START + "\n**0:00:00** Слова лектора.\n"
    assert not spook.meetings_dir.exists()


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
    assert list(spook.meetings_dir.iterdir()) == []  # Заметка отклонённой Сессии не остаётся
    assert spook.audio.active_sources == set()  # уже начатый захват Системного звука остановлен
    assert spook.start_lecture().status_code == 200  # Лекции Микрофон не нужен
