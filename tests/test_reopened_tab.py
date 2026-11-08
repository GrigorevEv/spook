"""Повторное открытие вкладки: Сессия живёт в службе, а не во вкладке (главный шов, два подключения к потоку)."""

from __future__ import annotations

from conftest import speech


def test_session_continues_after_all_tabs_closed_and_new_tab_gets_full_state(spook):
    # Между Репликами по 20 секунд тишины, чтобы каждая стала отдельным абзацем.
    spook.recognizer.say("При открытой вкладке.", "", "Пока вкладок не было.", "", "После новой вкладки.")
    spook.start_lecture()

    with spook.events() as first_tab:
        spook.audio.feed(speech(20))
        first_tab.wait_for("paragraph", containing="При открытой вкладке.")
    # Все вкладки закрыты: захват и распознавание идут дальше.
    spook.audio.feed(speech(40))
    spook.wait_until(lambda: "Пока вкладок не было." in spook.page().text)
    spook.clock.advance(65)

    with spook.events() as new_tab:
        state = new_tab.next_event()
        transcript = new_tab.next_event()
        spook.audio.feed(speech(40))
        added = new_tab.wait_for("paragraph", containing="После новой вкладки.")

    assert state.name == "state"
    assert "Лекция" in state.data and "идёт" in state.data
    assert ">0:01:05</time>" in state.data
    assert transcript.name == "transcript"
    assert transcript.data.count("При открытой вкладке.") == 1
    assert transcript.data.count("Пока вкладок не было.") == 1
    assert "После новой вкладки." not in transcript.data
    assert added.data.count("После новой вкладки.") == 1
    assert "Пока вкладок не было." not in added.data


def test_event_stream_after_server_restart_gets_full_state(spook):
    # Остановка сервера закрывает потоки событий; поднятый заново сервер снова их отдаёт —
    # так вкладка после обрыва переподключается и получает полное состояние.
    spook.recognizer.say("До обрыва.")
    spook.start_lecture()
    spook.audio.feed(speech(20))
    spook.wait_until(lambda: "До обрыва." in spook.page().text)

    spook.restart()

    with spook.events() as tab:
        state = tab.next_event()
        transcript = tab.next_event()

    assert state.name == "state" and "идёт" in state.data
    assert transcript.name == "transcript" and transcript.data.count("До обрыва.") == 1
