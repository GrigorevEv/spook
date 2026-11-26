from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from test_notes_screen import write_note

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)

WARNING = "Выжимка будет написана заново: прежняя Выжимка заменится новой."


def row_of(page: Page, topic: str):
    return page.get_by_role("row").filter(has_text=topic)


def test_restart_form_shows_warning_and_is_not_sent_until_confirmation_is_checked(spook, page, screenshot):
    note = write_note(
        spook.meetings_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "встреча", "## Суть\n\nПро Raft."
    )
    spook.agent.play("wait")
    sent = []
    page.on("request", lambda request: sent.append(request.url) if request.method == "POST" else None)
    page.goto(f"{spook.base_url}/notes")
    row = row_of(page, "Репликация в Raft")

    row.get_by_text("Перезапустить Агента").click()
    row.get_by_role("button", name="Запустить").click()

    expect(row.get_by_text(WARNING)).to_be_visible()
    screenshot(page, "перезапуск-без-флажка")

    row.get_by_role("checkbox", name="понимаю, запустить заново").check()
    row.get_by_role("button", name="Запустить").click()

    row = row_of(page, "Репликация в Raft")
    expect(row).to_contain_text("работает")
    spook.agent.release()
    expect(row).to_contain_text("готово")
    assert sent == [f"{spook.base_url}/notes/agent"]
    [call] = spook.agent.calls()
    assert call["input"]["topic"] == "Репликация в Raft"
    assert "## Суть" in note.read_text(encoding="utf-8")
