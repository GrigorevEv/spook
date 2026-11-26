from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from conftest import agent_model_and_effort, speech
from wisp_page import read_page

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)

def meeting_with_summary(spook) -> None:
    spook.agent.play("summary")
    spook.recognizer.say("Лидер принимает записи.")
    with spook.events() as events:
        assert spook.start_meeting(topic="Репликация в Raft").status_code == 200
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Лидер принимает записи.")
        assert spook.stop().status_code == 200
        events.wait_for("agent", containing="готово")


def row_of(page: Page, topic: str):
    return page.get_by_role("row").filter(has_text=topic)


def test_notes_screen_shows_summary_state_opens_agent_log_and_restarts_agent_only_after_confirmation(spook, page, screenshot):
    meeting_with_summary(spook)
    page.goto(spook.base_url)
    page.get_by_role("navigation", name="Экраны").get_by_role("link", name="Заметки").click()

    row = row_of(page, "Репликация в Raft")
    expect(row).to_contain_text("2026-11-06 14:02")
    expect(row).to_contain_text("Встреча")
    expect(row).to_contain_text("готово")
    screenshot(page, "заметки")
    [note] = spook.pages()
    row.get_by_role("button", name="Открыть в Wisp").click()
    expect(page.locator("#notice")).to_contain_text("Открываю в Wisp")
    [opened] = spook.opener.calls()
    assert opened["argv"] == [note.as_uri()]

    row.get_by_role("link", name="Журнал").click()
    expect(page.locator("body")).to_contain_text("Выжимка: готово")
    page.go_back()

    row = row_of(page, "Репликация в Raft")
    row.get_by_text("Перезапустить Агента").click()
    expect(row).to_contain_text("Выжимка будет написана заново")
    row.get_by_label("Модель").select_option("gpt-6.2-sol")
    row.get_by_label("Effort").select_option("low")
    understood = row.get_by_role("checkbox", name="понимаю, запустить заново")
    spook.agent.play("wait-updated")

    row.get_by_role("button", name="Запустить").click()

    assert understood.evaluate("box => box.validity.valueMissing")
    assert len(spook.agent.calls()) == 1

    understood.check()
    row.get_by_role("button", name="Запустить").click()

    row = row_of(page, "Репликация в Raft")
    expect(row).to_contain_text("работает")
    expect(row.get_by_text("Перезапустить Агента")).to_have_count(0)
    spook.agent.release()
    expect(row).to_contain_text("готово")
    expect(row.get_by_text("Перезапустить Агента")).to_be_visible()
    screenshot(page, "заметки-после-перезапуска")
    assert [agent_model_and_effort(call) for call in spook.agent.calls()] == [
        ("gpt-6.2-astra", "high"), ("gpt-6.2-sol", "low")
    ]
    assert spook.pages() == [note]
    assert "Выжимка обновлена." in read_page(note).summary


def test_settings_screen_saves_auto_stop_and_meeting_model_and_rejects_invalid_value_with_reason(spook, page, screenshot):
    page.goto(f"{spook.base_url}/settings")
    meeting = page.get_by_role("region", name="Встреча")
    auto_stop = page.get_by_role("textbox", name="Минут тишины до автостопа")
    expect(meeting.get_by_role("combobox", name="Модель Агента")).to_have_value("gpt-6.2-astra")
    expect(auto_stop).to_have_value("5")

    meeting.get_by_role("combobox", name="Модель Агента").fill("gpt-6.2-sol")
    auto_stop.fill("2,5")
    page.get_by_role("button", name="Сохранить").click()

    expect(page.get_by_role("status")).to_contain_text("Настройки сохранены")
    page.reload()
    expect(meeting.get_by_role("combobox", name="Модель Агента")).to_have_value("gpt-6.2-sol")
    expect(page.get_by_role("region", name="Лекция")).to_have_count(0)
    expect(auto_stop).to_have_value("2.5")
    saved = spook.settings_path.read_bytes()

    meeting.get_by_role("combobox", name="Модель Агента").fill("gpt-6.2-astra")
    auto_stop.fill("пять")
    page.get_by_role("button", name="Сохранить").click()

    expect(page.get_by_role("alert").filter(has_text="Время автостопа")).to_have_text(
        "Время автостопа — число минут больше нуля, а не «пять»"
    )
    expect(page.get_by_role("alert").filter(has_text="Настройки не сохранены")).to_be_visible()
    expect(auto_stop).to_have_value("пять")
    screenshot(page, "настройки-отказ")
    assert spook.settings_path.read_bytes() == saved
    page.goto(f"{spook.base_url}/settings")
    expect(meeting.get_by_role("combobox", name="Модель Агента")).to_have_value("gpt-6.2-sol")
    expect(auto_stop).to_have_value("2.5")
