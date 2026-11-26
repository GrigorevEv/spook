from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

from spook.settings import SessionKind
from spook.transcript import Mark, Paragraph

TRANSCRIPT_HEADING = "## Транскрипт"

TOPIC_MAX_LENGTH = 80
_FORBIDDEN_IN_NAMES = set('*"\\/<>:|?#^[]')

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2}) (.+)$")
_NO_TOPIC = re.compile("(?:" + "|".join(re.escape(kind.label) for kind in SessionKind) + r")(?: \(\d+\))?")


@dataclass(frozen=True)
class NoteName:
    date: str
    hour: str
    minute: str
    rest: str

    @property
    def prefix(self) -> str:
        return f"{self.date} {self.hour}-{self.minute}"

    @property
    def has_topic(self) -> bool:
        return _NO_TOPIC.fullmatch(self.rest) is None


def parse_name(stem: str) -> NoteName | None:
    match = _NAME.match(stem)
    return None if match is None else NoteName(*match.groups())


class NoteNotFound(Exception):
    pass


def find_session_note(raw: str | Path, folders: Iterable[tuple[SessionKind, Path]]) -> tuple[Path, SessionKind]:
    path = Path(raw)
    if not path.is_absolute():
        raise NoteNotFound(f"Нужен полный путь к Заметке: {raw}")
    if path.suffix != ".md" or parse_name(path.stem) is None:
        raise NoteNotFound(f"Это не Заметка Сессии: {raw}")
    for kind, folder in folders:
        try:
            if path.parent.resolve() != folder.resolve():
                continue
            candidate = folder / path.name
            links_outside = candidate.resolve().parent != folder.resolve()
            if links_outside or not candidate.is_file():
                raise NoteNotFound(f"Заметки нет: {path.name}")
            if "лекция" in read_properties(candidate.read_text(encoding="utf-8", errors="replace")).get("tags", ""):
                raise NoteNotFound(f"Это не Заметка Встречи: {path.name}")
        except OSError as error:
            raise NoteNotFound(f"Заметка недоступна: {error.strerror or error}") from error
        return candidate, kind
    raise NoteNotFound(f"Заметка вне папки Встреч: {raw}")


def read_properties(text: str) -> dict[str, str]:
    lines = text.split("\n")
    closing = _properties_end(lines)
    result: dict[str, str] = {}
    for line in lines[1:closing] if closing is not None else ():
        key, colon, value = line.partition(":")
        if colon:
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def transcript_line(lines: list[str]) -> int | None:
    try:
        return lines.index(TRANSCRIPT_HEADING)
    except ValueError:
        return None


def _properties_end(lines: list[str]) -> int | None:
    if not lines or lines[0] != "---":
        return None
    try:
        return lines.index("---", 1)
    except ValueError:
        return None


def _write_atomically(path: Path, text: str) -> None:
    hidden_temporary = path.with_name(f".{path.name}.tmp")
    try:
        hidden_temporary.write_text(text, encoding="utf-8")
        os.replace(hidden_temporary, path)
    finally:
        hidden_temporary.unlink(missing_ok=True)


def clean_topic(raw: str) -> str:
    replaced = "".join(" " if ch in _FORBIDDEN_IN_NAMES or not ch.isprintable() else ch for ch in raw)
    return " ".join(replaced.split())[:TOPIC_MAX_LENGTH].strip()


class Note:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def has_topic(self) -> bool:
        name = parse_name(self.path.stem)
        return name is None or name.has_topic

    def rename_with_topic(self, topic: str) -> Path:
        topic = clean_topic(topic)
        if not topic:
            raise ValueError("Тема пуста")
        name = parse_name(self.path.stem)
        if name is None:
            raise ValueError(f"Имя Заметки не по образцу «ГГГГ-ММ-ДД ЧЧ-ММ Тема»: {self.path.name}")
        target = next(
            candidate
            for candidate in _candidate_paths(self.path.parent, f"{name.prefix} {topic}")
            if candidate == self.path or not candidate.exists()
        )
        if target != self.path:
            self.path = self.path.rename(target)
        return self.path

    @classmethod
    def create(
        cls, folder: Path, started_at: datetime, kind: SessionKind, topic: str = ""
    ) -> Note:
        folder.mkdir(parents=True, exist_ok=True)
        header = (
            "---\n"
            f"date: {started_at:%Y-%m-%d}\n"
            f'start: "{started_at:%H:%M}"\n'
            f"tags: [{kind.tag}]\n"
            "---\n"
            "\n"
            f"{TRANSCRIPT_HEADING}\n"
        )
        for path in _candidate_paths(folder, f"{started_at:%Y-%m-%d %H-%M} {clean_topic(topic) or kind.label}"):
            try:
                with path.open("x", encoding="utf-8") as file:
                    file.write(header)
            except FileExistsError:
                continue
            return cls(path)
        raise AssertionError("unreachable")

    def append_paragraph(self, paragraph: Paragraph) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"\n{paragraph.as_markdown()}\n")

    def extend_paragraph(self, text: str) -> None:
        with self.path.open("r+b") as file:
            file.seek(-1, os.SEEK_END)
            ends_with_newline = file.read(1) == b"\n"
            file.seek(-1 if ends_with_newline else 0, os.SEEK_END)
            file.write(f" {text}\n".encode("utf-8"))

    def append_mark(self, mark: Mark) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"\n{mark.as_markdown()}\n")

    def set_end(self, ended_at: datetime) -> None:
        def edit(properties: list[str]) -> list[str]:
            kept = [line for line in properties if not line.startswith("end:")]
            start_index = next(i for i, line in enumerate(kept) if line.startswith("start:"))
            kept.insert(start_index + 1, f'end: "{ended_at:%H:%M}"')
            return kept

        self._edit_properties(edit)

    def _edit_properties(self, edit: Callable[[list[str]], list[str]]) -> None:
        lines = self.path.read_text(encoding="utf-8").split("\n")
        closing = _properties_end(lines)
        if closing is None:
            raise ValueError(f"У Заметки нет свойств: {self.path.name}")
        properties = edit(lines[1:closing])
        _write_atomically(self.path, "\n".join(["---", *properties, *lines[closing:]]))

    def transcript_markdown(self) -> str:
        lines = self.path.read_text(encoding="utf-8").split("\n")
        heading = transcript_line(lines)
        return "" if heading is None else "\n".join(lines[heading + 1 :])

    def summary(self) -> str:
        lines = self.path.read_text(encoding="utf-8").split("\n")
        heading = transcript_line(lines)
        closing = _properties_end(lines)
        if heading is None or (closing is None and lines[0] == "---"):
            return ""
        start = 0 if closing is None else closing + 1
        return "\n".join(lines[start:heading]).strip()


class TranscriptGuard:
    def __init__(self, original: str) -> None:
        self.snapshot = original
        lines = original.split("\n")
        start = transcript_line(lines)
        self._transcript = None if start is None else "\n".join(lines[start:])

    @classmethod
    def remember(cls, note_path: Path) -> TranscriptGuard:
        return cls(note_path.read_text(encoding="utf-8"))

    def restore(self, note_path: Path) -> str:
        if self._transcript is None:
            return ""
        try:
            current = note_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        lines = current.split("\n")
        start = transcript_line(lines)
        if start is not None:
            if "\n".join(lines[start:]) == self._transcript:
                return ""
            _write_atomically(note_path, "\n".join([*lines[:start], self._transcript]))
            return "Агент изменил Транскрипт — возвращён исходный."
        body = self._transcript.partition("\n")[2]
        kept = current.removesuffix(body).strip("\n")
        if not kept.strip():
            _write_atomically(note_path, self.snapshot)
            return "Агент стёр Заметку — восстановлены свойства и Транскрипт."
        _write_atomically(note_path, f"{kept}\n\n{self._transcript}")
        return "Агент стёр заголовок «Транскрипт» — Транскрипт возвращён на место."


def _candidate_paths(folder: Path, stem: str) -> Iterator[Path]:
    yield folder / f"{stem}.md"
    number = 2
    while True:
        yield folder / f"{stem} ({number}).md"
        number += 1
