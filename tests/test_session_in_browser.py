"""Сквозные сценарии в настоящем браузере: страница «Сессия» на службе с подменами.

Элементы выбираются по роли и тексту, а не по классам оформления.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from playwright.sync_api import Page, expect

from spook.audio import Source

from conftest import speech

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)


def status_of(page: Page):
    return page.get_by_label("Состояние Сессии")


def live_text_of(page: Page):
    return page.get_by_label("Живой текст")


def test_lecture_from_start_to_stop_on_demo_launch(demo, page, screenshot):
    page.goto(demo.base_url)
    expect(status_of(page)).to_contain_text("нет Сессии")

    page.get_by_role("button", name="Старт").click()

    expect(status_of(page)).to_contain_text("Лекция")
    expect(status_of(page)).to_contain_text("идёт")
    expect(live_text_of(page)).to_contain_text("Spook слушает звук на компьютере")
    expect(live_text_of(page)).to_contain_text("бывает двух видов: Встреча или Лекция")
    screenshot(page, "лекция-идёт")

    page.get_by_role("button", name="Стоп").click()

    expect(status_of(page)).to_contain_text("нет Сессии")
    expect(page.get_by_role("button", name="Старт")).to_be_visible()
    expect(live_text_of(page)).to_contain_text("Spook слушает звук на компьютере")
    timer = status_of(page).get_by_role("time")
    frozen = timer.text_content()
    page.wait_for_timeout(1500)
    assert timer.text_content() == frozen, "после «Стоп» таймер должен остановиться"
    screenshot(page, "после-стоп")


def test_meeting_from_start_through_pause_to_summary_and_opening_note_in_obsidian(spook, page, screenshot):
    spook.agent.play("summary")
    spook.recognizer.say("Коллеги, всем привет.", "Привет, начинаем.")  # Системный звук и Микрофон до Паузы, по 20 с
    spook.recognizer.say("Хорошо, продолжаем.")  # Системный звук после Паузы
    page.goto(spook.base_url)

    # Переключатели видов скрыты оформлением, пользователь нажимает на подпись.
    page.get_by_role("group", name="Вид Сессии").get_by_text("Встреча").click()
    expect(page.get_by_role("radio", name="Встреча")).to_be_checked()
    expect(page.get_by_role("textbox", name="Ссылка")).to_be_hidden()  # Ссылка нужна только Лекции
    page.get_by_role("textbox", name="Тема").fill("Планёрка")
    page.get_by_role("button", name="Старт").click()

    expect(status_of(page)).to_contain_text("Встреча")
    expect(status_of(page)).to_contain_text("идёт")
    expect(status_of(page).get_by_role("textbox", name="Тема")).to_have_value("Планёрка")
    spook.audio.feed(speech(20), Source.SYSTEM)
    spook.audio.feed(speech(20), Source.MIC)
    spook.clock.advance(20)
    expect(live_text_of(page)).to_contain_text("14:02 Собеседники Коллеги, всем привет.")
    expect(live_text_of(page)).to_contain_text("14:02 Я Привет, начинаем.")
    screenshot(page, "встреча-идёт")

    page.get_by_role("button", name="Пауза").click()
    expect(status_of(page)).to_contain_text("на Паузе")
    spook.clock.advance(5 * 60)
    page.get_by_role("button", name="Продолжить").click()

    expect(status_of(page)).to_contain_text("идёт")
    expect(live_text_of(page)).to_contain_text("Пауза 14:02–14:07")
    spook.audio.feed(speech(20), Source.SYSTEM)
    expect(live_text_of(page)).to_contain_text("14:07 Собеседники Хорошо, продолжаем.")

    page.get_by_role("button", name="Стоп").click()

    expect(status_of(page)).to_contain_text("нет Сессии")
    agent = page.get_by_label("Агент")
    expect(agent).to_contain_text("2026-11-06 14-02 Планёрка")
    expect(agent).to_contain_text("Агент: готово")
    screenshot(page, "встреча-выжимка-готова")
    text = live_text_of(page).inner_text()
    assert [text.count(said) for said in ("Коллеги, всем привет.", "Привет, начинаем.", "Хорошо, продолжаем.")] == [1, 1, 1]

    agent.get_by_role("button", name="Открыть в Obsidian").click()

    expect(page.get_by_role("alert").filter(has_text="Открываю в Obsidian")).to_contain_text("2026-11-06 14-02 Планёрка")
    note = spook.meetings_dir / "2026-11-06 14-02 Планёрка.md"
    [[uri]] = [call["argv"] for call in spook.opener.calls()]
    assert uri.startswith("obsidian://open?path=%2F") and unquote(uri.removeprefix("obsidian://open?path=")) == str(note)
    assert "Про тесты." in note.read_text(encoding="utf-8")  # Выжимка заглушки Агента


def test_closing_all_tabs_keeps_session_and_new_tab_shows_everything(spook, context, screenshot):
    # Между Репликами по 20 секунд тишины, чтобы каждая стала отдельным абзацем.
    spook.recognizer.say("При открытой вкладке.", "", "Пока вкладок не было.", "", "После новой вкладки.")
    tab = context.new_page()
    tab.goto(spook.base_url)
    tab.get_by_role("button", name="Старт").click()
    expect(status_of(tab)).to_contain_text("идёт")  # щелчок не ждёт ответа: без захвата звук пропал бы
    spook.audio.feed(speech(20))
    expect(live_text_of(tab)).to_contain_text("При открытой вкладке.")

    tab.close()  # все вкладки закрыты — захват и распознавание идут дальше
    spook.audio.feed(speech(40))
    spook.wait_until(lambda: "Пока вкладок не было." in spook.page().text)
    spook.clock.advance(65)

    new_tab = context.new_page()
    new_tab.goto(spook.base_url)

    expect(status_of(new_tab)).to_contain_text("Лекция")
    expect(status_of(new_tab)).to_contain_text("идёт")
    expect(status_of(new_tab).get_by_role("time")).to_have_text(re.compile(r"^0:01:0[5-9]$"))
    expect(live_text_of(new_tab)).to_contain_text("При открытой вкладке.")
    expect(live_text_of(new_tab)).to_contain_text("Пока вкладок не было.")
    spook.audio.feed(speech(40))
    expect(live_text_of(new_tab)).to_contain_text("После новой вкладки.")
    text = live_text_of(new_tab).inner_text()
    assert text.count("При открытой вкладке.") == 1
    assert text.count("Пока вкладок не было.") == 1
    assert text.count("После новой вкладки.") == 1
    screenshot(new_tab, "новая-вкладка")


def test_page_reconnects_by_itself_after_event_stream_breaks(spook, page, screenshot):
    spook.recognizer.say("До обрыва.", "", "Во время обрыва.", "", "После восстановления.")
    page.goto(spook.base_url)
    page.get_by_role("button", name="Старт").click()
    expect(status_of(page)).to_contain_text("идёт")  # щелчок не ждёт ответа: без захвата звук пропал бы
    spook.audio.feed(speech(20))
    expect(live_text_of(page)).to_contain_text("До обрыва.")
    page.evaluate("() => { window.reopened = 0; document.addEventListener('htmx:sseOpen', () => window.reopened++); }")

    spook.restart()  # служба перезапущена: поток событий оборван, Сессия продолжается
    spook.audio.feed(speech(40))
    spook.wait_until(lambda: "Во время обрыва." in spook.page().text)
    spook.clock.advance(80)

    page.wait_for_function("() => window.reopened > 0")
    expect(live_text_of(page)).to_contain_text("Во время обрыва.")
    expect(status_of(page)).to_contain_text("идёт")
    expect(status_of(page).get_by_role("time")).to_have_text(re.compile(r"^0:01:2\d$"))  # таймер взят с сервера
    spook.audio.feed(speech(40))
    expect(live_text_of(page)).to_contain_text("После восстановления.")
    text = live_text_of(page).inner_text()
    assert text.count("До обрыва.") == 1
    assert text.count("Во время обрыва.") == 1
    assert text.count("После восстановления.") == 1
    screenshot(page, "после-переподключения")
