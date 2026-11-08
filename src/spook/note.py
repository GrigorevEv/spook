"""Заметка в Хранилище: создаётся при «Старт», дописывается по ходу Сессии, получает `end` при завершении.

Единственный модуль, который знает формат Заметки:

- имя — `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>.md` по началу Сессии; без Темы на её месте вид Сессии
  (`parse_name`, `NoteName`); Заметка Сессии — такой файл прямо в папке вида (`find_session_note`);
- свойства — строки `ключ: значение` между `---` в начале (`read_properties`);
- заголовок `## Транскрипт` — граница: всё ниже принадлежит приложению, всё выше пишет Агент
  (`transcript_line`, `TranscriptGuard`).

Заметка перезаписывается целиком только атомарно — через временный файл в той же папке
и переименование: после сбоя на диске либо прежний текст, либо новый.
"""

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
# Знаки, недопустимые в именах файлов (Windows, macOS, Linux) и в именах заметок Obsidian,
# включая те, что ломают его ссылки `[[Заметка#Заголовок^блок|текст]]`.
_FORBIDDEN_IN_NAMES = set('*"\\/<>:|?#^[]')

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2}) (.+)$")
# Без Темы на её месте вид Сессии, возможно с номером ` (2)` при совпадении имён.
_NO_TOPIC = re.compile("(?:" + "|".join(re.escape(kind.label) for kind in SessionKind) + r")(?: \(\d+\))?")


@dataclass(frozen=True)
class NoteName:
    """Имя Заметки Сессии `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>` без `.md`."""

    date: str  # «2026-11-06»
    hour: str
    minute: str
    rest: str  # Тема или, пока её нет, вид Сессии (с номером при совпадении имён)

    @property
    def prefix(self) -> str:
        """Дата и время начала: «2026-11-06 14-02»."""
        return f"{self.date} {self.hour}-{self.minute}"

    @property
    def has_topic(self) -> bool:
        return _NO_TOPIC.fullmatch(self.rest) is None


def parse_name(stem: str) -> NoteName | None:
    """Имя Заметки Сессии по имени файла без `.md`; `None` — имя не по образцу."""
    match = _NAME.match(stem)
    return None if match is None else NoteName(*match.groups())


class NoteNotFound(Exception):
    """Путь не ведёт к Заметке Сессии в папке вида; текст — причина для пользователя."""


def find_session_note(raw: str | Path, folders: Iterable[tuple[SessionKind, Path]]) -> tuple[Path, SessionKind]:
    """Заметка Сессии по пути, названному командой, и вид её папки — одна проверка для всех команд.

    Принимается только полный путь к файлу `ГГГГ-ММ-ДД ЧЧ-ММ …md` прямо в одной из папок `folders`,
    без `..` и без символических ссылок наружу: пути сравниваются после `resolve`. Возвращает путь
    в папке, как она названа в `folders`; иначе — `NoteNotFound` с причиной."""
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
            if candidate.resolve().parent != folder.resolve() or not candidate.is_file():
                raise NoteNotFound(f"Заметки нет: {path.name}")
        except OSError as error:
            raise NoteNotFound(f"Заметка недоступна: {error.strerror or error}") from error
        return candidate, kind
    raise NoteNotFound(f"Заметка вне папок Встреч и Лекций: {raw}")


def read_properties(text: str) -> dict[str, str]:
    """Свойства из шапки `---`: `ключ: значение`, кавычки вокруг значения сняты; шапки нет — пусто."""
    lines = text.split("\n")
    closing = _properties_end(lines)
    result: dict[str, str] = {}
    for line in lines[1:closing] if closing is not None else ():
        key, colon, value = line.partition(":")
        if colon:
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def transcript_line(lines: list[str]) -> int | None:
    """Номер строки с заголовком «Транскрипт» или `None`, если его в Заметке нет."""
    try:
        return lines.index(TRANSCRIPT_HEADING)
    except ValueError:
        return None


def _properties_end(lines: list[str]) -> int | None:
    """Номер строки, закрывающей шапку свойств; `None` — шапки нет или она не закрыта."""
    if not lines or lines[0] != "---":
        return None
    try:
        return lines.index("---", 1)
    except ValueError:
        return None


def _write_atomically(path: Path, text: str) -> None:
    """Текст целиком — во временный файл рядом (скрытый, чтобы его не видели Obsidian и список
    Сессий), затем переименованием на место."""
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clean_topic(raw: str) -> str:
    """Тема, пригодная для имени Заметки: недопустимые и управляющие знаки заменены пробелом,
    пробелы схлопнуты, длина не больше `TOPIC_MAX_LENGTH`. Пустая строка — Темы нет."""
    replaced = "".join(" " if ch in _FORBIDDEN_IN_NAMES or not ch.isprintable() else ch for ch in raw)
    return " ".join(replaced.split())[:TOPIC_MAX_LENGTH].strip()


def clean_link(raw: str) -> str:
    """Ссылка без управляющих знаков и крайних пробелов; пустая строка — Ссылки нет."""
    return "".join(ch for ch in raw if ch.isprintable()).strip()


def _yaml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class Note:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def has_topic(self) -> bool:
        """Есть ли в имени Тема: пока её нет, на её месте стоит вид Сессии — «Встреча» или «Лекция»."""
        name = parse_name(self.path.stem)
        return name is None or name.has_topic

    def rename_with_topic(self, topic: str) -> Path:
        """Даёт Заметке имя `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>.md`, сохраняя дату и время из прежнего имени.

        Переиспользуется без HTTP — так Тему, придуманную Агентом, задаёт завершённой Заметке
        его команда. Тема очищается, как при вводе пользователем; при совпадении имён
        добавляется ` (2)`. Возвращает новый путь.
        """
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
        cls, folder: Path, started_at: datetime, kind: SessionKind, topic: str = "", link: str = ""
    ) -> Note:
        """Создаёт Заметку `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>.md`; без Темы на её месте вид Сессии.

        Ссылка, если есть, становится свойством `source`. При совпадении имён добавляется
        ` (2)`, ` (3)`, … Папку создаёт, если её нет. Ошибки файловой системы передаются
        вызывающему как есть.
        """
        folder.mkdir(parents=True, exist_ok=True)
        link = clean_link(link)
        header = (
            "---\n"
            f"date: {started_at:%Y-%m-%d}\n"
            f'start: "{started_at:%H:%M}"\n'
            f"tags: [{kind.tag}]\n"
            + (f"source: {_yaml_string(link)}\n" if link else "")
            + "---\n"
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
        """Новый абзац — отдельным блоком через пустую строку; Заметка всегда заканчивается переводом строки."""
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"\n{paragraph.as_markdown()}\n")

    def extend_paragraph(self, text: str) -> None:
        """Продолжение абзаца дописывается к последней строке Заметки; выше ничего не меняется."""
        with self.path.open("r+b") as file:
            file.seek(-1, os.SEEK_END)
            ends_with_newline = file.read(1) == b"\n"  # так всегда; перевод строки переносится за продолжение
            file.seek(-1 if ends_with_newline else 0, os.SEEK_END)
            file.write(f" {text}\n".encode("utf-8"))

    def append_mark(self, mark: Mark) -> None:
        """Отметка — отдельная строка курсивом после последнего абзаца."""
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"\n{mark.as_markdown()}\n")

    def set_end(self, ended_at: datetime) -> None:
        """Пишет свойство `end` после `start`; остальной текст Заметки не меняется."""

        def edit(properties: list[str]) -> list[str]:
            kept = [line for line in properties if not line.startswith("end:")]
            start_index = next(i for i, line in enumerate(kept) if line.startswith("start:"))
            kept.insert(start_index + 1, f'end: "{ended_at:%H:%M}"')
            return kept

        self._edit_properties(edit)

    def set_link(self, link: str) -> None:
        """Пишет свойство `source` последним из свойств; без Ссылки свойства нет."""
        link = clean_link(link)

        def edit(properties: list[str]) -> list[str]:
            kept = [line for line in properties if not line.startswith("source:")]
            return kept + ([f"source: {_yaml_string(link)}"] if link else [])

        self._edit_properties(edit)

    def _edit_properties(self, edit: Callable[[list[str]], list[str]]) -> None:
        """Переписывает строки свойств между `---` атомарно; остальной текст Заметки не меняется."""
        lines = self.path.read_text(encoding="utf-8").split("\n")
        closing = _properties_end(lines)
        if closing is None:
            raise ValueError(f"У Заметки нет свойств: {self.path.name}")
        properties = edit(lines[1:closing])
        _write_atomically(self.path, "\n".join(["---", *properties, *lines[closing:]]))

    def transcript_markdown(self) -> str:
        """Текст под заголовком «Транскрипт»; пустая строка, если заголовка нет."""
        lines = self.path.read_text(encoding="utf-8").split("\n")
        heading = transcript_line(lines)
        return "" if heading is None else "\n".join(lines[heading + 1 :])

    def summary(self) -> str:
        """Выжимка — текст между свойствами и заголовком «Транскрипт»; пустая строка, если её нет,
        заголовка в Заметке не осталось или шапка свойств не закрыта."""
        lines = self.path.read_text(encoding="utf-8").split("\n")
        heading = transcript_line(lines)
        closing = _properties_end(lines)
        if heading is None or (closing is None and lines[0] == "---"):
            return ""
        start = 0 if closing is None else closing + 1
        return "\n".join(lines[start:heading]).strip()


class TranscriptGuard:
    """Охрана Транскрипта: помнит всё от заголовка «Транскрипт» до конца Заметки и после работы
    Агента возвращает это на место, если текст изменён. Всё выше заголовка — Выжимка — остаётся
    таким, каким его оставил Агент. Если Агент стёр и заголовок, его текст остаётся целиком
    (без хвоста, совпадающего с исходным Транскриптом), а заголовок с Транскриптом встают в конец.
    Если Заметки не стало (удалена или пуста), она пишется заново такой, какой была до запуска.
    Заметка чинится атомарной перезаписью."""

    def __init__(self, original: str) -> None:
        self.snapshot = original  # текст Заметки до запуска
        lines = original.split("\n")
        start = transcript_line(lines)
        self._transcript = None if start is None else "\n".join(lines[start:])

    @classmethod
    def remember(cls, note_path: Path) -> TranscriptGuard:
        return cls(note_path.read_text(encoding="utf-8"))

    def restore(self, note_path: Path) -> str:
        """Сверяет Заметку с запомненным и чинит её, если нужно. Возвращает сообщение о том, что
        было сделано, или пустую строку, если Транскрипт не тронут."""
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
        body = self._transcript.partition("\n")[2]  # всё после строки заголовка
        kept = current.removesuffix(body).strip("\n")
        if not kept.strip():
            _write_atomically(note_path, self.snapshot)
            return "Агент стёр Заметку — восстановлены свойства и Транскрипт."
        _write_atomically(note_path, f"{kept}\n\n{self._transcript}")
        return "Агент стёр заголовок «Транскрипт» — Транскрипт возвращён на место."


def _candidate_paths(folder: Path, stem: str) -> Iterator[Path]:
    """`<stem>.md`, затем `<stem> (2).md`, `<stem> (3).md`, …"""
    yield folder / f"{stem}.md"
    number = 2
    while True:
        yield folder / f"{stem} ({number}).md"
        number += 1
