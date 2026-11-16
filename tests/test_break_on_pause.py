from __future__ import annotations

from conftest import note_text, speech

NOTE_NAME = "2026-11-06 14-02 Лекция"


def test_long_break_while_paused_finishes_session_at_last_activity_without_pause_mark(spook):
    spook.agent.play("summary")
    spook.recognizer.say("До Паузы.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До Паузы.")
    spook.pause()
    spook.clock.advance(100)

    spook.restart_service(60 * 60)

    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:04–15:04 — без речи дольше 5 минут"],
        ["Выжимка готова", NOTE_NAME],
    ]
    page = spook.page().text
    assert "нет Сессии" in page and ">Старт</button>" in page
    assert note_text(spook) == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:04"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** До Паузы.\n"
    )
    assert not (spook.state_dir / "session.json").exists()
