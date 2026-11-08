"""«Пауза» и «Продолжить» в Лекции — главный шов: HTTP, поток событий, Заметка."""

from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text, speech


def test_pause_closes_current_utterance_and_shows_state_on_pause(spook):
    spook.recognizer.say("Начало.", "Хвост.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(25))
        events.wait_for("paragraph", containing="Начало.")

        response = spook.pause()

        assert response.status_code == 200, response.text
        events.wait_for("state", containing="на Паузе")
        events.wait_for("paragraph-update", containing="Хвост.")

    # Хвост в 5 секунд закрыт Паузой и распознан; молчания перед ним нет, поэтому он продолжает абзац.
    assert [(heard.start, heard.duration) for heard in spook.recognizer.heard] == [(0.0, 20.0), (20.0, 5.0)]
    html = spook.page().text
    assert "на Паузе" in html
    assert ">Продолжить</button>" in html
    assert ">Пауза</button>" not in html
    assert ">Стоп</button>" in html and "disabled" not in html
    assert note_text(spook) == NOTE_AFTER_START + "\n**0:00:00** Начало. Хвост.\n"


def test_resume_leaves_pause_mark_and_sound_during_pause_is_not_transcribed(spook):
    spook.recognizer.say("Начало.", "Хвост.", "После Паузы.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(25))
        spook.clock.advance(25)
        events.wait_for("paragraph", containing="Начало.")
        spook.pause()
        events.wait_for("paragraph-update", containing="Хвост.")
        spook.audio.feed(speech(40))  # звук на Паузе пропадает, как в настоящей системе
        spook.clock.advance(5 * 60)

        response = spook.resume()

        assert response.status_code == 200, response.text
        events.wait_for("state", containing="идёт")
        mark = events.wait_for("mark", containing="Пауза")
        spook.audio.feed(speech(20))
        after = events.wait_for("paragraph", containing="После Паузы.")

    assert "<em>Пауза 0:00:25–0:05:25</em>" in mark.data
    assert "0:00:25" in after.data  # время от начала Сессии без учёта Паузы
    # Отметка закрывает абзац: Реплика после Паузы начинает новый, хотя молчания в захваченном времени нет.
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Начало. Хвост.\n"
        "\n*Пауза 0:00:25–0:05:25*\n"
        "\n**0:00:25** После Паузы.\n"
    )


def test_stop_from_pause_finishes_session_as_usual(spook):
    spook.recognizer.say("Начало.", "Хвост.", "Лишнее.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(25))
        events.wait_for("paragraph", containing="Начало.")
        spook.pause()
        events.wait_for("paragraph-update", containing="Хвост.")
        spook.audio.feed(speech(20))
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
    assert ">Старт</button>" in spook.page().text


def test_timer_stands_on_pause_and_excludes_pause_after_resume(spook):
    spook.start_lecture()
    spook.clock.advance(65)
    spook.pause()
    spook.clock.advance(10 * 60)

    paused = spook.page().text
    spook.resume()
    spook.clock.advance(5)
    resumed = spook.page().text

    assert 'data-running="false"' in paused and ">0:01:05</time>" in paused
    assert 'data-running="true"' in resumed and ">0:01:10</time>" in resumed


def test_reopened_page_gets_transcript_with_pause_mark_in_order(spook):
    spook.recognizer.say("До Паузы.", "После Паузы.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До Паузы.")
        spook.pause()
        spook.clock.advance(60)
        spook.resume()
        events.wait_for("mark", containing="Пауза")
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="После Паузы.")

    with spook.events() as events:
        state = events.next_event()
        transcript = events.next_event()

    assert state.name == "state" and "идёт" in state.data
    assert transcript.name == "transcript"
    assert (
        transcript.data.index("До Паузы.")
        < transcript.data.index("<em>Пауза 0:00:20–0:01:20</em>")
        < transcript.data.index("После Паузы.")
    )


def test_pause_and_resume_out_of_place_are_rejected_with_reason(spook):
    assert spook.pause().status_code == 409 and "Сессии нет" in spook.pause().text
    assert spook.resume().status_code == 409 and "Сессии нет" in spook.resume().text

    spook.start_lecture()
    response = spook.resume()
    assert response.status_code == 409 and "не на Паузе" in response.text

    spook.pause()
    response = spook.pause()
    assert response.status_code == 409 and "уже на Паузе" in response.text
    assert "на Паузе" in spook.page().text
