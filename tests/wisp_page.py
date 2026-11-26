from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta_parts: list[str] = []
        self.title_parts: list[str] = []
        self.parts: dict[str, list[str]] = {}
        self.section = ""
        self.field = ""
        self.first_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        identifier = dict(attrs).get("id")
        if tag == "script" and identifier == "wiki-meta":
            self.field = "meta"
        elif tag == "h1":
            self.field = "title"
        elif tag == "section":
            self.section = identifier or ""
            self.parts[self.section] = []
            self.first_heading = True
        elif self.first_heading and tag == "h2":
            self.first_heading = False
            self.field = "heading"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "h1", "h2"):
            self.field = ""
        if tag == "section":
            self.section = ""

    def handle_data(self, data: str) -> None:
        if self.field == "meta":
            self.meta_parts.append(data)
        elif self.field == "title":
            self.title_parts.append(data)
        elif self.section and not self.field:
            self.parts[self.section].append(data)


@dataclass
class MeetingPage:
    title: str
    meta: dict
    summary: str
    transcript: str

    @property
    def properties(self) -> dict:
        return self.meta.get("spook", {})


def read_page(path: Path) -> MeetingPage:
    parser = _PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()

    def section(name: str) -> str:
        lines = "".join(parser.parts.get(name, [])).splitlines()
        return "\n\n".join(line.strip() for line in lines if line.strip())

    return MeetingPage(
        title="".join(parser.title_parts),
        meta=json.loads("".join(parser.meta_parts) or "{}"),
        summary=section("выжимка"),
        transcript=section("транскрипт"),
    )
