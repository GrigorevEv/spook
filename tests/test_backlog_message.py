from __future__ import annotations

from conftest import speech

FULL_BACKLOG_MESSAGE = "Отставание распознавания 60 с. Текст появится позже, ничего не теряется."


def test_backlog_message_says_text_will_appear_later_and_nothing_is_lost(spook):
    spook.recognizer.say("Первая.", "Вторая.", "Третья.")
    spook.recognizer.hold()
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(60))
        waiting = events.wait_for("backlog", containing="60 с")
        page_while_waiting = spook.page().text
        spook.recognizer.release_all()
        events.wait_for("paragraph-update", containing="Третья.")

    assert f">{FULL_BACKLOG_MESSAGE}<" in waiting.data
    assert f">{FULL_BACKLOG_MESSAGE}<" in page_while_waiting
