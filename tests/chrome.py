"""Фикстуры браузерных тестов: системный Google Chrome через Playwright, браузеры не скачиваются.

Запуск: `uv run pytest -q -m browser`. Снимки сохраняются в папку из переменной окружения
`SPOOK_SCREENSHOTS`, иначе во временную папку теста. Подключаются один раз как модуль фикстур
(`pytest_plugins` в `tests/conftest.py`): вложенный `conftest.py` сломал бы `from conftest import …`
в остальных тестах, а импорт фикстур в каждый модуль заводил бы по своему Playwright на модуль —
второй падает внутри цикла событий первого.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

Screenshot = Callable[[Page, str], None]


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    if shutil.which("google-chrome") is None:
        pytest.skip("нужен системный Google Chrome")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = browser.new_context(
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1000, "height": 720},
    )
    yield context
    context.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    return context.new_page()


@pytest.fixture
def screenshot(tmp_path: Path) -> Screenshot:
    folder = Path(os.environ.get("SPOOK_SCREENSHOTS") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)

    def take(page: Page, name: str) -> None:
        page.screenshot(path=folder / f"{name}.png", full_page=True)

    return take
