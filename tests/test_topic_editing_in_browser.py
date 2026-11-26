from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)

def wait_for_state_with(page: Page, text: str) -> None:
    page.wait_for_function(
        "text => window.states.some(state => state.includes(text))",
        arg=text,
    )


def test_running_meeting_shows_its_topic_and_takes_edits_from_screen(spook, page):
    assert spook.start_meeting(topic="Репликация в Raft").status_code == 200
    page.goto(spook.base_url)
    page.evaluate(
        "() => { window.states = []; document.addEventListener('htmx:sseMessage', event => {"
        " if (event.detail.type === 'state') window.states.push(event.detail.data); }); }"
    )
    session = page.get_by_label("Состояние Сессии")
    topic = session.get_by_role("textbox", name="Тема")

    expect(topic).to_have_value("Репликация в Raft")
    expect(topic).to_be_editable()

    topic.fill("Консенсус в Raft")
    topic.blur()
    wait_for_state_with(page, 'value="Консенсус в Raft"')

    expect(topic).to_have_value("Консенсус в Raft")
    page.reload()
    expect(topic).to_have_value("Консенсус в Raft")
