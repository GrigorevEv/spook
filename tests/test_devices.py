from __future__ import annotations

import numpy as np

from conftest import NO_SPEECH, SAMPLE_RATE, speech
from test_sources_list import sources_on_screen
from spook.audio import Source


def pin_system_device(spook, device: str) -> None:
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write(f'\n[sources]\nsystem = "{device}"\n')


def test_capture_follows_default_device_change_without_stopping_session(spook):
    spook.recognizer.say("Начало.", "Хвост.", "После смены.")
    spook.start_meeting()

    with spook.events() as events:
        events.wait_for("state", containing="образцы")
        spook.feed_system(speech(25))
        spook.clock.advance(25)
        events.wait_for("paragraph", containing="Начало.")

        spook.audio.change_default(Source.SYSTEM, "Наушники USB")

        state = events.wait_for("state", containing="Наушники USB")
        mark = events.wait_for("mark", containing="Системный звук")
        spook.feed_system(speech(20))
        after = events.wait_for("paragraph", containing="После смены.")

    assert "идёт" in state.data
    assert sources_on_screen(state.data) == [("Системный звук", "Наушники USB", True), ("Микрофон", "образцы", True)]
    assert "<em>14:02 Системный звук переключён на «Наушники USB»</em>" in mark.data
    assert "14:02" in after.data
    html = spook.page().text
    assert "Наушники USB" in html
    assert 'class="warning"' not in html, "смена устройства по умолчанию — не повод для предупреждения"
    assert spook.note_page().transcript == (
        "14:02 Собеседники: Начало. Хвост.\n\n"
        "14:02 Системный звук переключён на «Наушники USB»\n\n"
        "14:02 Собеседники: После смены."
    )


def test_lost_device_leaves_mark_and_warning_and_capture_continues_from_new_default(spook):
    spook.audio.change_default(Source.MIC, "Микрофон USB")
    spook.recognizer.say("До пропажи.", "После пропажи.")
    spook.start_meeting()

    with spook.events() as events:
        events.wait_for("state", containing="образцы")
        spook.feed_system(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До пропажи.")

        spook.audio.lose_device("образцы", new_default="Динамики ноутбука")

        state = events.wait_for("state", containing="Динамики ноутбука")
        mark = events.wait_for("mark", containing="пропало")
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="После пропажи.")

    assert "идёт" in state.data
    assert "<em>14:02 Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»</em>" in mark.data
    html = spook.page().text
    assert '<p class="warning" role="alert">Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»</p>' in html
    assert 'title="Динамики ноутбука"' in html
    assert spook.note_page().transcript == (
        "14:02 Собеседники: До пропажи.\n\n"
        "14:02 Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»\n\n"
        "14:02 Собеседники: После пропажи."
    )


def test_warning_about_lost_device_stays_until_session_ends(spook):
    spook.start_meeting()
    with spook.events() as events:
        events.wait_for("state", containing="образцы")
        spook.audio.lose_device("образцы", new_default="Динамики ноутбука")
        events.wait_for("mark", containing="пропало")

    spook.pause()
    assert 'class="warning"' in spook.page().text, "предупреждение переживает Паузу"
    spook.resume()
    assert 'class="warning"' in spook.page().text

    spook.stop()
    with spook.events() as events:
        events.wait_for("state", containing="нет Сессии")
    assert 'class="warning"' not in spook.page().text

    spook.start_meeting()
    assert 'class="warning"' not in spook.page().text, "новая Сессия начинается без старых предупреждений"


def test_pinned_device_is_used_instead_of_default_and_ignores_default_changes(spook):
    pin_system_device(spook, "Виртуальное устройство")
    spook.start_meeting()

    with spook.events() as events:
        state = events.wait_for("state")
        spook.audio.change_default(Source.SYSTEM, "Наушники USB")
        spook.feed_system(speech(1))
        before_levels = []
        while (event := events.next_event()).name != "levels":
            before_levels.append(event.name)

    assert sources_on_screen(state.data) == [
        ("Системный звук", "Виртуальное устройство", True), ("Микрофон", "образцы", True),
    ]
    assert not {"state", "mark"} & set(before_levels), "смена умолчания закреплённого Источника не даёт ни состояния, ни отметки"
    html = spook.page().text
    assert 'title="Виртуальное устройство"' in html
    assert "Наушники USB" not in html
    assert spook.note_page().transcript == ""


def test_lost_pinned_device_leaves_mark_and_warning_and_capture_falls_back_to_default(spook):
    pin_system_device(spook, "Виртуальное устройство")
    spook.recognizer.say("После пропажи.")
    spook.start_meeting()

    with spook.events() as events:
        events.wait_for("state", containing="Виртуальное устройство")
        spook.feed_system(np.zeros(90 * SAMPLE_RATE, dtype=np.float32))
        spook.clock.advance(90)

        spook.audio.lose_device("Виртуальное устройство")

        state = events.wait_for("state", containing="образцы")
        mark = events.wait_for("mark", containing="пропало")
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="После пропажи.")
        spook.audio.change_default(Source.SYSTEM, "Наушники USB")
        events.wait_for("state", containing="Наушники USB")

    assert "идёт" in state.data
    assert "<em>14:03 Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»</em>" in mark.data
    assert "Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»</p>" in spook.page().text
    assert spook.note_page().transcript == (
        "14:03 Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»\n\n"
        "14:03 Собеседники: После пропажи.\n\n"
        "14:03 Системный звук переключён на «Наушники USB»"
    )


def test_lost_microphone_in_meeting_is_marked_by_clock_and_system_sound_keeps_its_device(spook):
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[sources]\nmic = "Гарнитура"\n')
    spook.recognizer.say("До пропажи.", NO_SPEECH, NO_SPEECH)
    spook.recognizer.say(NO_SPEECH, NO_SPEECH, NO_SPEECH)
    spook.start_meeting()

    with spook.events() as events:
        events.wait_for("state", containing="Гарнитура")
        spook.audio.feed(speech(60), Source.SYSTEM)
        spook.audio.feed(speech(60), Source.MIC)
        spook.clock.advance(60)
        events.wait_for("paragraph", containing="До пропажи.")

        spook.audio.lose_device("Гарнитура")

        mark = events.wait_for("mark", containing="пропало")
        spook.recognizer.say("После пропажи.")
        spook.audio.feed(speech(20), Source.MIC)
        events.wait_for("paragraph", containing="После пропажи.")

    expected = "Микрофон: устройство «Гарнитура» пропало, захват продолжается с «образцы»"
    assert f"<em>14:03 {expected}</em>" in mark.data
    html = spook.page().text
    assert f'<p class="warning" role="alert">{expected}</p>' in html
    assert "Гарнитура»" in html and 'title="Гарнитура"' not in html
    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}
    assert spook.note_page().transcript == (
        f"14:02 Собеседники: До пропажи.\n\n"
        f"14:03 {expected}\n\n"
        "14:03 Я: После пропажи."
    )
