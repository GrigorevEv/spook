from __future__ import annotations

from conftest import speech

NOTE_NAME = "2026-11-06 14-02 Встреча"


def test_long_break_while_paused_finishes_session_at_last_activity_without_pause_mark(spook):
    spook.agent.play("summary")
    spook.recognizer.say("До Паузы.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
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
    page = spook.note_page()
    assert page.properties["date"] == "2026-11-06"
    assert page.properties["start"] == "14:02"
    assert page.properties["end"] == "14:04"
    assert page.meta["tags"] == ["встреча"]
    assert page.transcript == "14:02 Собеседники: До Паузы."
    assert "Про тесты." in page.summary
    assert not (spook.state_dir / "session.json").exists()
