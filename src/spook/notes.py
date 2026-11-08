"""Список Сессий для экрана «Заметки»: собирается по файлам папок видов, базы данных нет.

Сессия в списке — файл `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>.md` в папке Встреч или Лекций из настроек;
остальные заметки Хранилища в список не попадают. Удалили файл — Сессии в списке нет.

Состояние Выжимки берётся из очереди Агента, если в нынешней работе службы у Заметки был
запуск, иначе по файлу: Выжимка над Транскриптом есть — «готово», нет — «нет».
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from spook.agent import AgentJob, AgentQueue, SummaryState
from spook.note import Note, NoteNotFound, find_session_note, parse_name, read_properties
from spook.settings import EFFORTS, SessionKind, Settings

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class NoteEntry:
    """Сессия в списке: что показать в строке и какие действия доступны."""

    path: Path
    kind: SessionKind
    started: str  # «2026-11-06 14:02»
    topic: str  # пустая строка — Темы нет
    duration: str  # пустая строка — конца нет (Сессия идёт или прервана)
    summary: SummaryState
    message: str = ""  # причина ошибки Агента
    warning: str = ""  # предупреждение Агента (охрана Транскрипта)
    has_summary: bool = False  # Выжимка в файле есть — запуск Агента напишет её заново
    has_log: bool = False
    in_session: bool = False  # Заметка идущей Сессии: Агента для неё не запускают
    folder_kind: SessionKind | None = None  # вид папки Заметки: по нему — журнал Агента; `None` — как `kind`

    @property
    def log_kind(self) -> SessionKind:
        return self.folder_kind or self.kind

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def agent_busy(self) -> bool:
        return self.summary in (SummaryState.QUEUED, SummaryState.RUNNING)


OPEN_TIMEOUT_SECONDS = 10
WIKI_TIMEOUT_SECONDS = 30


class NoteRefused(Exception):
    """Команда экрана «Заметки» отклонена; текст — причина для пользователя."""


def find_note(settings: Settings, raw: str) -> tuple[Path, SessionKind]:
    """Заметка, названная командой, и вид её папки: только Заметка Сессии прямо в папке Встреч
    или Лекций из настроек (`note.find_session_note`)."""
    try:
        return find_session_note(raw, settings.kind_folders())
    except NoteNotFound as error:
        raise NoteRefused(str(error)) from error


def obsidian_uri(path: Path) -> str:
    """Адрес Заметки для Obsidian: `obsidian://open?path=<абсолютный путь>`, путь целиком закодирован."""
    return "obsidian://open?path=" + quote(str(path), safe="")


def open_uri(program: str, uri: str) -> None:
    """Открывает адрес системным средством открытия (`[programs].open`): браузер со страницы
    `http://` локальные файлы и `obsidian://` сам не откроет."""
    try:
        completed = subprocess.run(
            [program, uri],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=OPEN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NoteRefused(f"Не удалось вызвать {program}: {error}") from error
    if completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", errors="replace").strip() or f"код возврата {completed.returncode}"
        raise NoteRefused(f"{program} не открыл адрес: {reason}")


def wiki_page_uri(command: str, note_name: str) -> str:
    """Адрес страницы Вики для Заметки Лекции — у инструмента Вики (`<command> file <имя заметки>`),
    а не вычисленный приложением. Инструмент печатает `file://`-адрес; если страницы нет, он
    отвечает ненулевым кодом и причиной."""
    argv = [os.path.expanduser(part) for part in shlex.split(command)] + ["file", note_name]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=WIKI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise NoteRefused(f"Инструмент Вики не запустился: {error}") from error
    if completed.returncode != 0:
        reason = _last_line(completed.stderr) or f"код возврата {completed.returncode}"
        raise NoteRefused(f"Страница Вики не найдена: {reason}")
    uri = _last_line(completed.stdout)
    if not uri.startswith("file://"):
        raise NoteRefused(f"Инструмент Вики не назвал страницу: {uri or 'пустой ответ'}")
    return uri


REWRITE_WARNING = "Выжимка будет написана заново: прежняя Выжимка заменится новой"
# Модель и effort идут флагами запуска: только имя, без пробелов и без ведущего «-».
_AGENT_OPTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")


def run_agent(
    settings: Settings,
    agent: AgentQueue,
    raw_note: str,
    model: str,
    effort: str,
    confirmed: bool,
    session_note: Path | None = None,
) -> Path:
    """«Запустить» или «Перезапустить Агента»: Заметка встаёт в очередь со Скиллом и папкой своего
    вида, но с моделью и effort, выбранными на этот запуск. Если Выжимка в Заметке уже есть, она
    будет написана заново — без подтверждения пользователя (`confirmed`) запуск отклоняется.

    Отклоняется и для Заметки идущей Сессии, и пока Заметка уже ждёт Агента или он с ней работает.
    Возвращает путь Заметки."""
    path, kind = find_note(settings, raw_note)
    if session_note is not None and path == session_note:
        raise NoteRefused("Сессия с этой Заметкой ещё идёт")
    current = agent.job_for(path)
    if current is not None and current.state in (SummaryState.QUEUED, SummaryState.RUNNING):
        raise NoteRefused(f"Агент для этой Заметки уже {current.state.label}")
    model, effort = model.strip(), effort.strip()
    if not _AGENT_OPTION.match(model):
        raise NoteRefused(f"Недопустимая модель: {model or 'не выбрана'}")
    if not _AGENT_OPTION.match(effort):
        raise NoteRefused(f"Недопустимый effort: {effort or 'не выбран'}")
    if _summary(Note(path)) and not confirmed:
        raise NoteRefused(f"{REWRITE_WARNING}. Подтвердите перезапуск")
    job = AgentJob.for_note(path, kind, settings.for_kind(kind))
    agent.enqueue(replace(job, model=model, effort=effort))
    return path


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


MODELS = ("opus", "sonnet", "haiku")


@dataclass(frozen=True)
class AgentChoice:
    """Выбор модели и effort на один запуск Агента; по умолчанию — из настроек вида."""

    models: tuple[str, ...]
    efforts: tuple[str, ...]
    model: str
    effort: str


def agent_choices(settings: Settings) -> dict[str, AgentChoice]:
    """Варианты для формы запуска по видам; значение из настроек всегда среди вариантов."""
    choices = {}
    for kind in SessionKind:
        kind_settings = settings.for_kind(kind)
        choices[kind.value] = AgentChoice(
            models=_with(MODELS, kind_settings.model),
            efforts=_with(EFFORTS, kind_settings.effort),
            model=kind_settings.model,
            effort=kind_settings.effort,
        )
    return choices


def _with(options: tuple[str, ...], value: str) -> tuple[str, ...]:
    return options if value in options else (*options, value)


def list_notes(settings: Settings, agent: AgentQueue, session_note: Path | None = None) -> list[NoteEntry]:
    """Сессии обеих папок видов от новых к старым. `session_note` — Заметка идущей Сессии."""
    entries: list[NoteEntry] = []
    seen: set[Path] = set()
    for kind in SessionKind:
        folder = settings.for_kind(kind).folder
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for path in files:
            if path.suffix != ".md" or parse_name(path.stem) is None or not path.is_file():
                continue
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            entry = describe(path, kind, agent, session_note)
            if entry is not None:
                entries.append(entry)
    return sorted(entries, key=lambda entry: (entry.started, entry.name), reverse=True)


def describe(path: Path, folder_kind: SessionKind, agent: AgentQueue, session_note: Path | None = None) -> NoteEntry | None:
    """Строка списка для Заметки; `None`, если файла уже нет."""
    name = parse_name(path.stem)
    if name is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    properties = read_properties(text)
    start = properties.get("start") or f"{name.hour}:{name.minute}"
    note = Note(path)
    has_summary = bool(_summary(note))
    job = agent.job_for(path)
    if job is not None:
        summary, message, warning = job.state, job.message, job.warning
    else:
        summary, message, warning = (SummaryState.DONE if has_summary else SummaryState.NONE), "", ""
    return NoteEntry(
        path=path,
        kind=_kind(properties.get("tags", ""), folder_kind),
        started=f"{properties.get('date') or name.date} {start}",
        topic=name.rest if name.has_topic else "",
        duration=_duration(start, properties.get("end", "")),
        summary=summary,
        message=message,
        warning=warning,
        has_summary=has_summary,
        has_log=agent.has_log(path.stem, folder_kind),
        folder_kind=folder_kind,
        in_session=session_note is not None and path == session_note,
    )


def _summary(note: Note) -> str:
    try:
        return note.summary()
    except (OSError, UnicodeDecodeError):
        return ""


def _kind(tags: str, folder_kind: SessionKind) -> SessionKind:
    """Вид — по тегу, который пишет приложение; без него — по папке."""
    for kind in SessionKind:
        if re.search(rf"(?<!\w){kind.tag}(?!\w)", tags):
            return kind
    return folder_kind


def _duration(start: str, end: str) -> str:
    """Длительность по свойствам `start` и `end`; через полночь — тоже. Пусто, если конца нет."""
    start_match, end_match = _TIME.match(start), _TIME.match(end)
    if start_match is None or end_match is None:
        return ""
    minutes = (int(end_match[1]) * 60 + int(end_match[2])) - (int(start_match[1]) * 60 + int(start_match[2]))
    minutes %= 24 * 60
    if minutes == 0:
        return "меньше минуты"
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60:02d} мин"
