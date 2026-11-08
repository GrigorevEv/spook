"""Автостоп — главный шов: HTTP, поток событий, Заметка, заглушка уведомлений, управляемые часы.

Время двигают только часы теста: автостоп не ждёт настоящих минут.
"""

from __future__ import annotations

from spook.audio import Source

from conftest import meeting_note_text, note_text, speech

NOTE_NAME = "2026-11-06 14-02 Лекция"


def test_silence_longer_than_auto_stop_finishes_session_like_stop_and_notifies(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Единственная Реплика.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Единственная Реплика.")
        spook.clock.advance(5 * 60 - 1)
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="завершается")
        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")

    assert (spook.lectures_dir / f"{NOTE_NAME}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:07"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Единственная Реплика.\n"
    )
    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "5 минут без речи"],
        ["Выжимка готова", NOTE_NAME],
    ]
    assert ">Старт</button>" in spook.page().text


def test_speech_restarts_countdown_but_segment_without_speech_does_not(spook):
    spook.recognizer.say("Сказано на четвёртой минуте.", "")
    spook.start_lecture()

    with spook.events() as events:
        spook.clock.advance(4 * 60)
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Сказано на четвёртой минуте.")
        spook.clock.advance(4 * 60)  # восемь минут от «Старт», но четыре — от Реплики
        assert "идёт" in spook.page().text
        spook.audio.feed(speech(20))  # отрезок без речи: распознаватель вернул пустой текст
        spook.wait_until(lambda: len(spook.recognizer.heard) == 2)
        assert "идёт" in spook.page().text

        spook.clock.advance(60)  # пять минут от Реплики

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:11"' in (spook.lectures_dir / f"{NOTE_NAME}.md").read_text(encoding="utf-8")


def test_speech_in_microphone_alone_restarts_countdown_in_meeting(spook):
    spook.recognizer.say("Я говорю на четвёртой минуте.", "")  # Микрофон, затем Системный звук без речи
    spook.start_meeting()

    with spook.events() as events:
        spook.clock.advance(4 * 60)
        spook.audio.feed(speech(20), Source.MIC)
        spook.wait_until(lambda: len(spook.recognizer.heard) == 1)
        spook.audio.feed(speech(20), Source.SYSTEM)  # у собеседников тишина
        spook.wait_until(lambda: "Я говорю на четвёртой минуте." in meeting_note_text(spook))
        spook.clock.advance(4 * 60)  # восемь минут от «Старт», но четыре — от речи в Микрофоне
        assert "идёт" in spook.page().text

        spook.clock.advance(60)

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:11"' in meeting_note_text(spook)


def test_pause_time_does_not_count_and_countdown_restarts_after_resume(spook):
    spook.recognizer.say("До Паузы.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="До Паузы.")
        spook.clock.advance(3 * 60)
        spook.pause()
        spook.clock.advance(60 * 60)  # час на Паузе — не тишина
        assert "на Паузе" in spook.page().text

        assert spook.resume().status_code == 200
        spook.clock.advance(5 * 60 - 1)  # от «Продолжить» 4:59, хотя от Реплики — больше часа
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="нет Сессии")

    assert note_text(spook) == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "15:10"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** До Паузы.\n"
        "\n*Пауза 0:03:00–1:03:00*\n"
    )


def test_auto_stop_time_comes_from_settings_and_counts_from_start_without_speech(spook):
    spook.write_settings(auto_stop_minutes=1.5)
    spook.start_lecture()

    with spook.events() as events:
        spook.clock.advance(89)
        assert "идёт" in spook.page().text

        spook.clock.advance(1)

        events.wait_for("state", containing="нет Сессии")

    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Сессия завершена", "90 секунд без речи"]]
    # Сессия без Реплик: Агент не запущен, пустой Заметки нет — как при «Стоп».
    assert spook.agent.calls() == [] and list(spook.lectures_dir.iterdir()) == []
