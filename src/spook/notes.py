from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from spook.agent import AgentJob, AgentQueue, SummaryState
from spook.note import Note, NoteNotFound, find_session_note, note_properties
from spook.settings import EFFORTS, MODELS, SessionKind, Settings, codex_model

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class NoteEntry:
    path: Path
    kind: SessionKind
    started: str
    topic: str
    duration: str
    summary: SummaryState
    message: str = ""
    warning: str = ""
    has_summary: bool = False
    has_log: bool = False
    in_session: bool = False
    folder_kind: SessionKind | None = None

    @property
    def log_kind(self) -> SessionKind:
        return self.folder_kind or self.kind

    @property
    def name(self) -> str:
        return Note(self.path).name

    @property
    def agent_busy(self) -> bool:
        return self.summary in (SummaryState.QUEUED, SummaryState.RUNNING)


OPEN_TIMEOUT_SECONDS = 10


class NoteRefused(Exception):
    pass


def find_note(settings: Settings, raw: str) -> tuple[Path, SessionKind]:
    try:
        return find_session_note(raw, settings.kind_folders())
    except NoteNotFound as error:
        raise NoteRefused(str(error)) from error


def open_uri(program: str, uri: str) -> None:
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


REWRITE_WARNING = "Выжимка будет написана заново: прежняя Выжимка заменится новой"
_SAFE_FLAG_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")


def run_agent(
    settings: Settings,
    agent: AgentQueue,
    raw_note: str,
    model: str,
    effort: str,
    confirmed: bool,
    session_note: Path | None = None,
) -> Path:
    path, kind = find_note(settings, raw_note)
    if session_note is not None and path == session_note:
        raise NoteRefused("Сессия с этой Заметкой ещё идёт")
    current = agent.job_for(path)
    if current is not None and current.state in (SummaryState.QUEUED, SummaryState.RUNNING):
        raise NoteRefused(f"Агент для этой Заметки уже {current.state.label}")
    model, effort = model.strip(), effort.strip()
    if not _SAFE_FLAG_VALUE.fullmatch(model) or codex_model(model) != model:
        raise NoteRefused(f"Недопустимая модель: {model or 'не выбрана'}")
    if not _SAFE_FLAG_VALUE.match(effort):
        raise NoteRefused(f"Недопустимый effort: {effort or 'не выбран'}")
    if _summary(Note(path)) and not confirmed:
        raise NoteRefused(f"{REWRITE_WARNING}. Подтвердите перезапуск")
    job = AgentJob.for_note(path, kind, settings.for_kind(kind))
    agent.enqueue(replace(job, model=model, effort=effort))
    return path


@dataclass(frozen=True)
class AgentChoice:
    models: tuple[str, ...]
    efforts: tuple[str, ...]
    model: str
    effort: str


def agent_choices(settings: Settings) -> dict[str, AgentChoice]:
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
    entries: list[NoteEntry] = []
    folder = settings.wisp / "html"
    try:
        files = list(folder.glob("[0-9][0-9][0-9][0-9]-*.html"))
    except OSError:
        files = []
    if session_note is not None and Note(session_note).is_draft:
        files.append(session_note)
    for path in files:
        if path.name == "index.html" or path.is_symlink() or not path.is_file():
            continue
        entry = describe(path, SessionKind.MEETING, agent, session_note)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda entry: (entry.started, entry.name), reverse=True)


def describe(path: Path, folder_kind: SessionKind, agent: AgentQueue, session_note: Path | None = None) -> NoteEntry | None:
    note = Note(path)
    try:
        properties = note_properties(note.read())
        if not properties:
            return None
        has_summary = bool(note.summary())
    except (OSError, UnicodeError):
        return None
    start = properties["start"]
    job = agent.job_for(path)
    if job is not None:
        summary, message, warning = job.state, job.message, job.warning
    else:
        summary, message, warning = (SummaryState.DONE if has_summary else SummaryState.NONE), "", ""
    return NoteEntry(
        path=path,
        kind=folder_kind,
        started=f"{properties['date']} {start}",
        topic=properties["topic"],
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


def _duration(start: str, end: str) -> str:
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
