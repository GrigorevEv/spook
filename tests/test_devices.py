"""Смена, пропажа и закрепление устройства — главный шов: подменённый вход разыгрывает события устройств."""

from __future__ import annotations

from conftest import MEETING_NOTE_AFTER_START, NOTE_AFTER_START, meeting_note_text, note_text, speech
from spook.audio import Source


def pin_system_device(spook, device: str) -> None:
    """Закрепляет устройство Системного звука в файле настроек — так, как это сделал бы пользователь."""
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write(f'\n[sources]\nsystem = "{device}"\n')


def test_capture_follows_default_device_change_without_stopping_session(spook):
    spook.recognizer.say("Начало.", "Хвост.", "После смены.")
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("state", containing="образцы")
        spook.audio.feed(speech(25))
        spook.clock.advance(25)
        events.wait_for("paragraph", containing="Начало.")

        spook.audio.change_default(Source.SYSTEM, "Наушники USB")

        state = events.wait_for("state", containing="Наушники USB")
        mark = events.wait_for("mark", containing="Системный звук")
        spook.audio.feed(speech(20))
        after = events.wait_for("paragraph", containing="После смены.")

    assert "идёт" in state.data and "образцы" not in state.data
    assert "<em>0:00:25 Системный звук переключён на «Наушники USB»</em>" in mark.data
    assert "0:00:25" in after.data
    html = spook.page().text
    assert "Наушники USB" in html
    assert 'class="warning"' not in html, "смена устройства по умолчанию — не повод для предупреждения"
    # Звук со старого устройства закрыт отметкой: Реплика с нового устройства начинает новый абзац.
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Начало. Хвост.\n"
        "\n*0:00:25 Системный звук переключён на «Наушники USB»*\n"
        "\n**0:00:25** После смены.\n"
    )


def test_lost_device_leaves_mark_and_warning_and_capture_continues_from_new_default(spook):
    spook.recognizer.say("До пропажи.", "После пропажи.")
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("state", containing="образцы")
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До пропажи.")

        spook.audio.lose_device("образцы", new_default="Динамики ноутбука")

        state = events.wait_for("state", containing="Динамики ноутбука")
        mark = events.wait_for("mark", containing="пропало")
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="После пропажи.")

    assert "идёт" in state.data
    assert "<em>0:00:20 Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»</em>" in mark.data
    html = spook.page().text
    assert '<p class="warning" role="alert">Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»</p>' in html
    assert 'title="Динамики ноутбука"' in html
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** До пропажи.\n"
        "\n*0:00:20 Системный звук: устройство «образцы» пропало, захват продолжается с «Динамики ноутбука»*\n"
        "\n**0:00:20** После пропажи.\n"
    )


def test_warning_about_lost_device_stays_until_session_ends(spook):
    spook.start_lecture()
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

    spook.start_lecture()
    assert 'class="warning"' not in spook.page().text, "новая Сессия начинается без старых предупреждений"


def test_pinned_device_is_used_instead_of_default_and_ignores_default_changes(spook):
    pin_system_device(spook, "Виртуальное устройство")
    spook.start_lecture()

    with spook.events() as events:
        state = events.wait_for("state")
        spook.audio.change_default(Source.SYSTEM, "Наушники USB")
        spook.audio.feed(speech(1))
        before_levels = []
        while (event := events.next_event()).name != "levels":
            before_levels.append(event.name)

    assert "Виртуальное устройство" in state.data and "образцы" not in state.data
    assert not {"state", "mark"} & set(before_levels), "смена умолчания закреплённого Источника не даёт ни состояния, ни отметки"
    html = spook.page().text
    assert 'title="Виртуальное устройство"' in html
    assert "Наушники USB" not in html
    assert note_text(spook) == NOTE_AFTER_START


def test_lost_pinned_device_leaves_mark_and_warning_and_capture_falls_back_to_default(spook):
    pin_system_device(spook, "Виртуальное устройство")
    spook.recognizer.say("После пропажи.")
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("state", containing="Виртуальное устройство")
        spook.clock.advance(90)

        spook.audio.lose_device("Виртуальное устройство")

        state = events.wait_for("state", containing="образцы")
        mark = events.wait_for("mark", containing="пропало")
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="После пропажи.")
        # Закреплённого устройства больше нет: до конца Сессии захват следует за устройством по умолчанию.
        spook.audio.change_default(Source.SYSTEM, "Наушники USB")
        events.wait_for("state", containing="Наушники USB")

    assert "идёт" in state.data
    assert "<em>0:01:30 Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»</em>" in mark.data
    assert "Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»</p>" in spook.page().text
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n*0:01:30 Системный звук: устройство «Виртуальное устройство» пропало, захват продолжается с «образцы»*\n"
        "\n**0:00:00** После пропажи.\n"
        "\n*0:01:30 Системный звук переключён на «Наушники USB»*\n"
    )


def test_lost_microphone_in_meeting_is_marked_by_clock_and_system_sound_keeps_its_device(spook):
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[sources]\nmic = "Гарнитура"\n')
    spook.recognizer.say("До пропажи.", "", "")  # Системный звук, 0–60 с
    spook.recognizer.say("", "", "")  # Микрофон, 0–60 с
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
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** До пропажи.\n"
        f"\n*14:03 {expected}*\n"
        "\n**14:03 Я:** После пропажи.\n"
    )
