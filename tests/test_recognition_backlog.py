"""Отставание распознавания — главный шов: медленный сценарный распознаватель, поток событий, Заметка.

Медленный распознаватель — сценарный, задержанный «воротами»: отрезки ждут, пока тест их не отпустит,
так что реального времени тест не ждёт. Реплики здесь идут без молчания между ними и потому
склеиваются в один абзац: продолжение приходит событием `paragraph-update`.
"""

from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text, speech


def test_slow_recognizer_loses_nothing_and_stop_waits_for_all_utterances_in_order(spook):
    spook.recognizer.say("Первая.", "Вторая.", "Третья.", "Четвёртая.", "Хвост.")
    spook.recognizer.hold()  # распознаватель не отвечает, пока тест его не отпустит
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(85))  # четыре отрезка по 20 с и остаток 5 с ждут в очереди
        spook.clock.advance(85)
        response = spook.stop()
        assert response.status_code == 200, response.text
        events.wait_for("state", containing="завершается")
        assert note_text(spook) == NOTE_AFTER_START  # ничего не распознано и ничего не выброшено

        spook.recognizer.release_all()

        events.wait_for("state", containing="нет Сессии")

    assert [heard.start for heard in spook.recognizer.heard] == [0.0, 20.0, 40.0, 60.0, 80.0]
    assert note_text(spook) == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:03"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Первая. Вторая. Третья. Четвёртая. Хвост.\n"
    )


def test_backlog_is_shown_only_while_a_segment_waits_behind_the_one_being_recognized(spook):
    spook.recognizer.say("Первая.", "Вторая.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("backlog")  # пустое отставание из полного состояния при подключении
        spook.audio.feed(speech(20))  # один отрезок: распознаётся, за ним никто не ждёт
        alone = events.wait_for("backlog")
        page_alone = spook.page().text

        spook.audio.feed(speech(20))  # второй отрезок ждёт за первым

        waiting = events.wait_for("backlog")
        page_waiting = spook.page().text
        spook.recognizer.release()  # первый распознан; второй распознаётся, за ним никто не ждёт
        events.wait_for("paragraph", containing="Первая.")
        alone_again = events.wait_for("backlog")
        spook.recognizer.release_all()

    assert "Отставание" not in alone.data and "Отставание" not in page_alone
    assert "Отставание распознавания 40 с" in waiting.data
    assert "Отставание распознавания 40 с" in page_waiting
    assert "Отставание" not in alone_again.data


def test_event_stream_reports_backlog_in_seconds_of_sound_and_page_clears_it_when_queue_is_empty(spook):
    spook.recognizer.say("Первая.", "Вторая.", "Третья.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(60))  # три отрезка по 20 с: один распознаётся, два ждут в очереди
        waiting = events.wait_for("backlog", containing="60 с")
        page_while_waiting = spook.page().text

        spook.recognizer.release()
        events.wait_for("paragraph", containing="Первая.")
        shrunk = events.wait_for("backlog", containing="40 с")

        spook.recognizer.release(2)
        events.wait_for("paragraph-update", containing="Третья.")
        cleared = events.next_event()

    assert "Отставание распознавания 60 с" in waiting.data and "ничего не теряется" in waiting.data
    assert "Отставание распознавания 60 с" in page_while_waiting
    assert "Отставание распознавания 40 с" in shrunk.data
    assert cleared.name == "backlog" and "Отставание" not in cleared.data
    assert "Отставание" not in spook.page().text


def test_while_finishing_page_shows_how_much_sound_is_left_to_recognize(spook):
    spook.recognizer.say("Первая.", "Вторая.", "Хвост.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(45))  # два отрезка по 20 с; остаток 5 с закроет «Стоп»
        spook.clock.advance(45)
        events.wait_for("backlog", containing="40 с")

        spook.stop()

        events.wait_for("state", containing="завершается")
        left = events.wait_for("backlog", containing="45 с")
        page_while_finishing = spook.page().text
        spook.recognizer.release()
        events.wait_for("paragraph", containing="Первая.")
        less = events.wait_for("backlog", containing="25 с")
        spook.recognizer.release()
        events.wait_for("paragraph-update", containing="Вторая.")
        last = events.wait_for("backlog")  # остался один отрезок — в «завершается» он всё равно виден
        spook.recognizer.release_all()
        events.wait_for("state", containing="нет Сессии")

    assert "Дорабатывается очередь распознавания: осталось 45 с." in left.data
    assert "завершается" in page_while_finishing
    assert "Дорабатывается очередь распознавания: осталось 45 с." in page_while_finishing
    assert "осталось 25 с" in less.data
    assert "осталось 5 с" in last.data
    assert "осталось" not in spook.page().text and "нет Сессии" in spook.page().text


def test_on_pause_backlog_stays_visible_until_queue_is_empty(spook):
    spook.recognizer.say("Первая.", "Вторая.", "Хвост.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(45))  # два отрезка по 20 с; остаток 5 с закроет «Пауза»
        spook.clock.advance(45)
        events.wait_for("backlog", containing="40 с")

        spook.pause()

        events.wait_for("state", containing="на Паузе")
        on_pause = events.wait_for("backlog", containing="45 с")
        page_on_pause = spook.page().text
        spook.recognizer.release_all()
        events.wait_for("paragraph-update", containing="Хвост.")
        cleared = events.next_event()

    assert "Отставание распознавания 45 с" in on_pause.data
    assert "на Паузе" in page_on_pause and "Отставание распознавания 45 с" in page_on_pause
    assert cleared.name == "backlog" and "Отставание" not in cleared.data
    page_later = spook.page().text
    assert "на Паузе" in page_later and "Отставание" not in page_later


def test_reopened_event_stream_starts_with_full_state_including_backlog(spook):
    spook.recognizer.say("Первая.", "Вторая.")
    spook.recognizer.hold()
    spook.start_lecture()
    spook.audio.feed(speech(40))

    with spook.events() as events:
        opening = [events.next_event() for _ in range(4)]

    assert [event.name for event in opening] == ["state", "transcript", "agent", "backlog"]
    assert "идёт" in opening[0].data
    assert "Отставание распознавания 40 с" in opening[-1].data
    spook.recognizer.release_all()
