from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)

NOTE = "2026-11-06 14-02 Репликация в Raft.md"


def wait_for_state_with(page: Page, text: str) -> None:
    page.wait_for_function(
        "text => window.states.some(state => state.includes(text))",
        arg=text,
    )


def test_running_lecture_shows_its_topic_and_link_and_takes_edits_from_screen(spook, page):
    assert spook.start_lecture(topic="Репликация в Raft", link="https://example.org/talk").status_code == 200
    page.goto(spook.base_url)
    page.evaluate(
        "() => { window.states = []; document.addEventListener('htmx:sseMessage', event => {"
        " if (event.detail.type === 'state') window.states.push(event.detail.data); }); }"
    )
    session = page.get_by_label("Состояние Сессии")
    topic = session.get_by_role("textbox", name="Тема")
    link = session.get_by_role("textbox", name="Ссылка")

    expect(topic).to_have_value("Репликация в Raft")
    expect(link).to_have_value("https://example.org/talk")
    expect(topic).to_be_editable()
    expect(link).to_be_editable()

    topic.fill("Консенсус в Raft")
    topic.blur()
    wait_for_state_with(page, 'value="Консенсус в Raft"')
    link.fill("https://example.org/raft")
    link.blur()
    wait_for_state_with(page, 'value="https://example.org/raft"')

    expect(topic).to_have_value("Консенсус в Raft")
    expect(link).to_have_value("https://example.org/raft")
    html = spook.page().text
    assert 'value="Консенсус в Raft"' in html and 'value="https://example.org/raft"' in html
    assert 'source: "https://example.org/raft"' in (spook.lectures_dir / NOTE).read_text(encoding="utf-8")
