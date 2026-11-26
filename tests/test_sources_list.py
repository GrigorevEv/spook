from __future__ import annotations

import re
from html import unescape


def sources_on_screen(page: str) -> list[tuple[str, str, bool]]:
    [listing] = re.findall(r'<ul class="sources" aria-label="Источники">(.*?)</ul>', page, flags=re.S)
    rows = []
    for row in re.findall(r'<li class="source".*?</li>', listing, flags=re.S):
        name = re.search(r'<span class="source-name">([^<]*)</span>', row).group(1)
        device = re.search(r'<span class="device"[^>]*>([^<]*)</span>', row).group(1)
        rows.append((unescape(name), unescape(device), 'role="meter"' in row))
    return rows


def test_meeting_lists_two_sources_system_sound_and_microphone(spook):
    spook.start_meeting()

    assert sources_on_screen(spook.page().text) == [("Системный звук", "образцы", True), ("Микрофон", "образцы", True)]
