from __future__ import annotations

from html.parser import HTMLParser

REMINDER = "Права Агента здесь не задаются: они записаны отдельно, в шапке Скилла."


class Sections(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.texts: dict[str, str] = {}
        self._section: list[str] | None = None
        self._heading: list[str] | None = None
        self._section_name = ""
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "section":
            self._section = []
        elif tag == "h2" and self._section is not None:
            self._heading = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._heading is not None:
            self._section_name = "".join(self._heading).strip()
            self._heading = None
        elif tag == "section" and self._section is not None:
            self.texts[self._section_name] = " ".join(" ".join(self._section).split())
            self._section = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        if self._section is not None:
            self._section.append(data)


def test_reminder_that_agent_permissions_live_in_skill_header_is_shown_in_meeting_and_lecture_sections(spook):
    response = spook.http.get(f"{spook.base_url}/settings")

    assert response.status_code == 200
    sections = Sections(response.text).texts
    assert REMINDER in sections["Встреча"]
    assert REMINDER in sections["Лекция"]
    assert response.text.count(REMINDER) == 2
