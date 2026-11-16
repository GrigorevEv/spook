from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.browser

SCREENS = ("/", "/notes", "/settings")
PALETTE_WORDS = re.compile(r"палитр|(?<![а-яё])т[её]мн|(?<![а-яё])светл|ночн|дневн|оформлени|dark|light|theme", re.I)
CONTROLS = ("button", "checkbox", "radio", "switch", "combobox", "link", "menuitem", "option")


def brightness(page: Page, property_name: str) -> float:
    color = page.evaluate(f"() => getComputedStyle(document.body).{property_name}")
    red, green, blue = (int(part) for part in re.findall(r"\d+", color)[:3])
    return (red + green + blue) / (3 * 255)


def palette_switches(page: Page) -> int:
    return page.get_by_role("switch").count() + sum(
        page.get_by_role(role, name=PALETTE_WORDS).count() for role in CONTROLS
    )


def test_screens_follow_dark_system_setting_without_own_palette_switch(spook, page, screenshot):
    page.emulate_media(color_scheme="dark")

    for path in SCREENS:
        page.goto(f"{spook.base_url}{path}")
        assert brightness(page, "backgroundColor") < 0.2, path
        assert brightness(page, "color") > 0.8, path
        assert palette_switches(page) == 0, path
    screenshot(page, "тёмная-палитра")

    page.emulate_media(color_scheme="light")

    for path in SCREENS:
        page.goto(f"{spook.base_url}{path}")
        assert brightness(page, "backgroundColor") > 0.8, path
        assert brightness(page, "color") < 0.2, path
