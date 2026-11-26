from __future__ import annotations

import re
from datetime import datetime
from html import escape, unescape
from itertools import count
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from jinja2 import Environment, PackageLoader, select_autoescape
from markdown_it import MarkdownIt

from spook import wisp
from spook.settings import SessionKind
from spook.transcript import Mark, Paragraph

TOPIC_MAX_LENGTH = 80
_FORBIDDEN_IN_NAMES = set('*"\\/<>:|?#^[]')
_SUMMARY = "выжимка"
_TRANSCRIPT = "транскрипт"
_DRAFTS = ".spook"
_TEMPLATE = Environment(loader=PackageLoader("spook", "templates"), autoescape=select_autoescape(["html"]))
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable("image")


class NoteNotFound(Exception):
    pass


def clean_topic(raw: str) -> str:
    replaced = "".join(" " if ch in _FORBIDDEN_IN_NAMES or not ch.isprintable() else ch for ch in raw)
    return " ".join(replaced.split())[:TOPIC_MAX_LENGTH].strip()


def note_properties(page: str) -> dict:
    meta = wisp.page_meta(page)
    data = meta.get("spook")
    if meta.get("section") != wisp.SECTION or not isinstance(data, dict):
        return {}
    if not all(isinstance(data.get(key), str) for key in ("id", "date", "start", "end", "topic")):
        return {}
    if not re.fullmatch("[0-9a-f]{32}", data["id"]):
        return {}
    return data


def find_session_note(raw: str | Path, folders: Iterable[tuple[SessionKind, Path]]) -> tuple[Path, SessionKind]:
    path = Path(raw)
    if not path.is_absolute():
        raise NoteNotFound(f"Нужен полный путь к Заметке: {raw}")
    if path.suffix != ".html" or path.name == "index.html":
        raise NoteNotFound(f"Это не Заметка Встречи: {raw}")
    for kind, folder in folders:
        try:
            if path.parent.resolve() != folder.resolve():
                continue
            candidate = folder / path.name
            if candidate.is_symlink() or not candidate.is_file():
                raise NoteNotFound(f"Заметки нет: {path.name}")
            if not note_properties(candidate.read_text(encoding="utf-8")):
                raise NoteNotFound(f"Это не Заметка Встречи: {raw}")
        except (OSError, UnicodeError) as error:
            raise NoteNotFound(f"Заметка недоступна: {error}") from error
        return candidate, kind
    raise NoteNotFound(f"Заметка вне Wisp: {raw}")


def _name(properties: dict) -> str:
    topic = properties["topic"] or "Встреча"
    return f"{properties['date']} {properties['start'].replace(':', '-')} {topic}"


def _candidate_names(name: str):
    while len(wisp.page_filename(name)) > 238:
        name = name[:-1].rstrip()
    yield name
    for number in count(2):
        yield f"{name} ({number})"


def _with_description(page: str, topic: str, body: str) -> str:
    summary = " ".join(wisp.text_content(body).split())
    description = " · ".join(part for part in (topic, summary) if part)[:240]
    return re.sub(r'<meta name="description" content="[^"]*">',
                  lambda _: f'<meta name="description" content="{escape(description)}">', page, count=1)


class Note:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def is_draft(self) -> bool:
        return self.path.parent.name == _DRAFTS

    @property
    def folder(self) -> Path:
        return self.path.parent.parent / "html" if self.is_draft else self.path.parent

    def read(self) -> str:
        if self.path.is_symlink():
            raise OSError(f"Путь Заметки ведёт на другую страницу: {self.path.name}")
        return self.path.read_bytes().decode("utf-8")

    @property
    def name(self) -> str:
        try:
            page = self.read()
            heading = re.search(r"<h1>(.*?)</h1>", page, flags=re.S)
            return wisp.text_content(heading[1]) if heading else self.path.stem
        except (OSError, UnicodeError):
            return self.path.stem

    @property
    def topic(self) -> str:
        return note_properties(self.read())["topic"]

    @property
    def date(self) -> str:
        return note_properties(self.read())["date"]

    @property
    def has_topic(self) -> bool:
        return bool(self.topic)

    @classmethod
    def create(cls, folder: Path, started_at: datetime, kind: SessionKind, topic: str = "") -> Note:
        css, engine = wisp.page_style(folder)
        drafts = folder.parent / _DRAFTS
        if drafts.is_symlink():
            raise OSError("Рабочая папка Wisp ведёт по ссылке")
        drafts.mkdir(mode=0o700, exist_ok=True)
        properties = {"id": uuid4().hex, "date": f"{started_at:%Y-%m-%d}", "start": f"{started_at:%H:%M}",
                      "end": "", "topic": clean_topic(topic)}
        meta = dict.fromkeys(("level", "parent", "source", "status", "version", "order"))
        meta.update(title=_name(properties), section=wisp.SECTION, sub="", tags=[kind.tag], aliases=[], term=False,
                    updated=properties["date"], spook=properties)
        page = _TEMPLATE.get_template("wisp_note.html").render(
            meta=meta, metadata=wisp.json_html(meta), css=css, engine=engine,
        ) + "\n"
        path = drafts / (properties["id"] + ".html")
        wisp.write_page(path, page, create=True)
        return cls(path)

    def rename_with_topic(self, topic: str) -> Path:
        topic = clean_topic(topic)
        if not topic:
            raise ValueError("Тема пуста")
        with wisp.write_lock(self.folder):
            original = self.read()
            meta = wisp.page_meta(original)
            properties = meta["spook"]
            properties["topic"] = topic
            name = _name(properties)
            if self.is_draft:
                meta["title"] = name
            page = wisp.with_title(original, name)
            page = _with_description(page, topic, wisp.content(page, _SUMMARY))
            page = wisp.with_meta(page, meta)
            wisp.write_page(self.path, page)
        if not self.is_draft:
            self._refresh_or_restore(original, page)
        return self.path

    def _refresh_or_restore(self, original: str, updated: str) -> None:
        try:
            wisp.refresh_index(self.path)
        except OSError:
            with wisp.write_lock(self.folder):
                try:
                    unchanged = self.read() == updated
                except (OSError, UnicodeError):
                    unchanged = False
                if unchanged:
                    wisp.write_page(self.path, original)
            raise

    def append_paragraph(self, paragraph: Paragraph) -> None:
        heading = f"{paragraph.label} {paragraph.speaker}:"
        self._append(f"<p><strong>{escape(heading)}</strong> {escape(paragraph.text)}</p>")

    def append_mark(self, mark: Mark) -> None:
        self._append(f"<p><em>{escape(mark.label)}</em></p>")

    def _append(self, html: str) -> None:
        page = self.read()
        _, end = wisp.section(page, _TRANSCRIPT)
        wisp.write_page(self.path, page[:end] + html + "\n" + page[end:])

    def extend_paragraph(self, text: str) -> None:
        page = self.read()
        start, end = wisp.section(page, _TRANSCRIPT)
        closing = page.rfind("</p>", start, end)
        if closing == -1:
            raise OSError("В Транскрипте отсутствует абзац")
        wisp.write_page(self.path, page[:closing] + " " + escape(text) + page[closing:])

    def set_end(self, ended_at: datetime) -> None:
        page = self.read()
        meta = wisp.page_meta(page)
        properties = meta["spook"]
        properties["end"] = f"{ended_at:%H:%M}"
        period = f"{properties['date']} · {properties['start']}–{properties['end']}"
        page = re.sub(r'(<p class="sub" id="meeting-time">).*?(</p>)',
                      lambda m: m[1] + escape(period) + m[2], page, count=1, flags=re.S)
        wisp.write_page(self.path, wisp.with_meta(page, meta))

    def publish(self) -> Path:
        if not self.is_draft:
            wisp.refresh_index(self.path)
            return self.path
        page = self.read()
        meta = wisp.page_meta(page)
        folder = self.folder
        with wisp.write_lock(folder):
            aliases = {
                alias.casefold()
                for entry in wisp.index_entries(folder)
                for alias in (entry.get("aliases") or []) if isinstance(alias, str)
            }
            for name in _candidate_names(_name(meta["spook"])):
                path = folder / wisp.page_filename(name)
                if name.casefold() in aliases and not path.exists():
                    continue
                meta["title"] = name
                published = wisp.with_meta(wisp.with_title(page, name), meta)
                try:
                    wisp.write_page(path, published, create=True)
                except FileExistsError:
                    if path.is_symlink() or not path.is_file():
                        continue
                    if note_properties(path.read_text(encoding="utf-8", errors="replace")).get("id") != meta["spook"]["id"]:
                        continue
                break
        wisp.refresh_index(path)
        self.path = path
        return path

    def remove_draft(self) -> None:
        note_id = note_properties(self.read())["id"]
        draft = self.folder.parent / _DRAFTS / f"{note_id}.html"
        draft.unlink(missing_ok=True)

    def transcript_markdown(self) -> str:
        body = wisp.content(self.read(), _TRANSCRIPT)
        lines = []
        for row in re.findall(r"<p>(.*?)</p>", body, flags=re.S):
            paragraph = re.fullmatch(r"<strong>(.*?)</strong> (.*)", row, flags=re.S)
            if paragraph:
                lines.append(f"**{unescape(paragraph[1])}** {unescape(paragraph[2])}")
            elif mark := re.fullmatch(r"<em>(.*?)</em>", row, flags=re.S):
                lines.append(f"*{unescape(mark[1])}*")
        return "\n\n".join(lines)

    def summary(self) -> str:
        return wisp.content(self.read(), _SUMMARY)

    def same_summary(self, summary: str) -> bool:
        return _MARKDOWN.render(summary.strip()).strip() == self.summary()

    def set_summary(self, summary: str) -> None:
        summary = summary.strip()
        if not summary or "## Транскрипт" in summary.splitlines():
            raise ValueError("Недопустимая Выжимка")
        body = _MARKDOWN.render(summary)
        with wisp.write_lock(self.folder):
            original = self.read()
            page = wisp.with_content(original, _SUMMARY, body)
            page = _with_description(page, note_properties(page)["topic"], body)
            wisp.write_page(self.path, page)
        self._refresh_or_restore(original, page)


class TranscriptGuard:
    def __init__(self, original: str) -> None:
        self.snapshot = original
        self._transcript = wisp.content(original, _TRANSCRIPT)

    @classmethod
    def remember(cls, note_path: Path, snapshot_path: Path) -> TranscriptGuard:
        guard = cls(Note(note_path).read())
        snapshot_path.parent.mkdir(mode=0o700, exist_ok=True)
        wisp.write_page(snapshot_path, guard.snapshot, create=True)
        return guard

    @staticmethod
    def snapshot_path(note_path: Path, snapshot_id: str) -> Path:
        if not re.fullmatch("[0-9a-f]{32}", snapshot_id) or note_path.parent.name != "html":
            raise ValueError("Некорректный идентификатор снимка Транскрипта")
        folder = note_path.parent.parent / _DRAFTS
        if folder.is_symlink():
            raise OSError("Рабочая папка Wisp ведёт по ссылке")
        return folder / f"agent-{snapshot_id}.guard"

    @classmethod
    def recover(cls, note_path: Path, snapshot_path: Path) -> str:
        if snapshot_path.is_symlink():
            raise OSError("Снимок Транскрипта ведёт по ссылке")
        try:
            original = snapshot_path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return ""
        title = wisp.page_meta(original).get("title")
        if not isinstance(title, str) or wisp.page_filename(title) != note_path.name or not note_properties(original):
            raise ValueError("Снимок Транскрипта принадлежит другой Заметке")
        return cls(original).restore(note_path)

    def restore(self, note_path: Path) -> str:
        with wisp.write_lock(note_path.parent):
            if note_path.is_symlink():
                raise OSError(f"Путь Заметки ведёт на другую страницу: {note_path.name}")
            try:
                page = Note(note_path).read()
                if wisp.content(page, _TRANSCRIPT) == self._transcript:
                    return ""
                restored = wisp.with_content(page, _TRANSCRIPT, self._transcript)
            except (OSError, UnicodeError):
                restored = self.snapshot
            wisp.write_page(note_path, restored)
        try:
            wisp.refresh_index(note_path)
        except OSError as error:
            raise OSError("Не удалось вернуть восстановленную Заметку в раздел «Встречи» Wisp") from error
        return "Агент изменил Транскрипт — возвращён исходный."
