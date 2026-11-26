from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from queue import Queue
from typing import BinaryIO, Callable

from spook import codex
from spook.meeting_context import meeting_input
from spook.note import Note, TranscriptGuard
from spook.notify import send_notification
from spook.settings import KindSettings, SessionKind, Settings, codex_model

log = logging.getLogger(__name__)

KILL_GRACE_SECONDS = 10
RECENT_JOBS = 5


class SummaryState(StrEnum):
    NONE = "none"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            SummaryState.NONE: "нет",
            SummaryState.QUEUED: "в очереди",
            SummaryState.RUNNING: "работает",
            SummaryState.DONE: "готово",
            SummaryState.FAILED: "ошибка",
        }[self]


@dataclass(frozen=True)
class AgentJob:
    note_path: Path
    kind: SessionKind
    skill: str
    model: str
    effort: str

    @classmethod
    def for_note(cls, note_path: Path, kind: SessionKind, settings: KindSettings) -> AgentJob:
        return cls(
            note_path=note_path,
            kind=kind,
            skill=settings.skill,
            model=settings.model,
            effort=settings.effort,
        )

    @property
    def note_name(self) -> str:
        return self.note_path.stem

@dataclass(frozen=True)
class AgentResult:
    state: SummaryState
    message: str = ""
    returncode: int | None = None
    warning: str = ""


@dataclass(frozen=True)
class AgentJobView:
    job: AgentJob
    state: SummaryState
    message: str = ""
    warning: str = ""

    @property
    def note_name(self) -> str:
        return self.job.note_name

    @property
    def has_log(self) -> bool:
        return self.state in (SummaryState.RUNNING, SummaryState.DONE, SummaryState.FAILED)


@dataclass
class _Record:
    job: AgentJob
    state: SummaryState = SummaryState.QUEUED
    message: str = ""
    warning: str = ""
    snapshot: str | None = None

    def view(self) -> AgentJobView:
        return AgentJobView(self.job, self.state, self.message, self.warning)

    def to_json(self) -> dict[str, object]:
        return {
            "note": str(self.job.note_path),
            "kind": self.job.kind.value,
            "skill": self.job.skill,
            "model": self.job.model,
            "effort": self.job.effort,
            "state": self.state.value,
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_json(cls, data: dict) -> _Record:
        job = AgentJob(
            note_path=Path(data["note"]),
            kind=SessionKind(data["kind"]),
            skill=data["skill"],
            model=codex_model(data["model"]),
            effort=data["effort"],
        )
        return cls(job, SummaryState(data["state"]), snapshot=data.get("snapshot"))


INTERRUPTED = "Агент прерван перезапуском службы"
QUEUE_NAME = "queue.json"


class AgentQueue:
    def __init__(self, load_settings: Callable[[], Settings], log_dir: Path) -> None:
        self._load_settings = load_settings
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._records: list[_Record] = []
        self._pending: Queue[_Record] = Queue()
        self._worker: threading.Thread | None = None
        self.note_lock = threading.RLock()
        self.on_topic: Callable[[Path, str], str]
        self.on_change: Callable[[], None] = lambda: None

    def enqueue(self, job: AgentJob) -> None:
        self._enqueue(_Record(job))

    def _enqueue(self, record: _Record) -> None:
        with self._lock:
            self._records.append(record)
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="spook-agent", daemon=True)
                self._worker.start()
            self._persist_under_lock()
        self._pending.put(record)
        self.on_change()

    def restore(self) -> None:
        path = self._log_dir / QUEUE_NAME
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            saved = [_Record.from_json(item) for item in items if item["kind"] == SessionKind.MEETING]
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Очередь Агента в %s не прочитана — удалена", path)
            path.unlink(missing_ok=True)
            return
        for record in saved:
            if record.state is SummaryState.RUNNING:
                self._interrupted(record)
            elif not record.job.note_path.is_file():
                log.warning("Заметки %s больше нет — из очереди Агента убрана", record.job.note_path)
            else:
                self._enqueue(_Record(record.job))
        with self._lock:
            self._persist_under_lock()

    def _interrupted(self, record: _Record) -> None:
        job = record.job
        warning = TranscriptGuard(record.snapshot).restore(job.note_path) if record.snapshot is not None else ""
        result = AgentResult(SummaryState.FAILED, INTERRUPTED, warning=warning)
        log.warning("Агент закончил: %s — %s: %s", job.note_name, result.state.label, INTERRUPTED)
        try:
            journal_path = self.log_path(job.note_name, job.kind)
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with journal_path.open("a", encoding="utf-8") as journal:
                if warning:
                    journal.write(f"\nОхрана Транскрипта: {warning}\n")
                journal.write(_closing_line(result))
        except OSError:
            log.exception("Журнал Агента %s не дописан", job.note_name)
        with self._lock:
            self._records.append(replace(record, state=result.state, message=result.message, warning=warning, snapshot=None))
        self.on_change()
        self._notify(job, result)

    def _persist_under_lock(self) -> None:
        alive = [record.to_json() for record in self._records if record.state in (SummaryState.QUEUED, SummaryState.RUNNING)]
        path = self._log_dir / QUEUE_NAME
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(alive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            log.exception("Очередь Агента не записана в %s", path)

    def jobs(self) -> tuple[AgentJobView, ...]:
        with self._lock:
            latest: dict[Path, AgentJobView] = {}
            for record in reversed(self._records):
                latest.setdefault(record.job.note_path, record.view())
            return tuple(latest.values())[:RECENT_JOBS]

    def job_for(self, note_path: Path) -> AgentJobView | None:
        with self._lock:
            for record in reversed(self._records):
                if record.job.note_path == note_path:
                    return record.view()
        return None

    def note_renamed(self, old_path: Path, new_path: Path, kind: SessionKind) -> None:
        with self._lock:
            for record in self._records:
                if record.job.note_path == old_path:
                    record.job = replace(record.job, note_path=new_path)
            old_log = self._existing_log(old_path.stem, kind)
            new_log = self.log_path(new_path.stem, kind)
            if old_log is not None and old_log != new_log:
                old_log.replace(new_log)
            self._persist_under_lock()
        self.on_change()

    def log_path(self, note_name: str, kind: SessionKind) -> Path:
        return self._log_dir / kind.value / f"{note_name}.log"

    def _existing_log(self, note_name: str, kind: SessionKind | None) -> Path | None:
        if _escapes_log_dir(note_name):
            return None
        kinds = [kind] if kind is not None else list(SessionKind)
        legacy_log = self._log_dir / f"{note_name}.log"
        for path in [*(self.log_path(note_name, each) for each in kinds), legacy_log]:
            if path.is_file():
                return path
        return None

    def has_log(self, note_name: str, kind: SessionKind) -> bool:
        return self._existing_log(note_name, kind) is not None

    def read_log(self, note_name: str, kind: SessionKind | None = None) -> str | None:
        path = self._existing_log(note_name, kind)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _work(self) -> None:
        while True:
            record = self._pending.get()
            self._set(record, SummaryState.RUNNING)
            job = self._current_job(record)
            log.info("Агент запущен: %s — %s, %s, %s", job.note_name, job.skill, job.model, job.effort)
            try:
                result = self._run(record)
            except Exception as error:
                log.exception("Запуск Агента для %s сорвался", record.job.note_name)
                result = AgentResult(SummaryState.FAILED, f"Сбой запуска: {error}")
            self._set(record, result.state, result.message, result.warning)
            job = self._current_job(record)
            reason = f": {result.message}" if result.message else ""
            level = logging.INFO if result.state is SummaryState.DONE else logging.WARNING
            log.log(level, "Агент закончил: %s — %s%s", job.note_name, result.state.label, reason)
            self._notify(job, result)

    def _set(self, record: _Record, state: SummaryState, message: str = "", warning: str = "") -> None:
        with self._lock:
            record.state = state
            record.message = message
            record.warning = warning
            if state is not SummaryState.RUNNING:
                record.snapshot = None
            self._persist_under_lock()
        self.on_change()

    def _current_job(self, record: _Record) -> AgentJob:
        with self._lock:
            return record.job

    def _run(self, record: _Record) -> AgentResult:
        settings = self._load_settings()
        timeout = settings.agent.timeout_minutes * 60
        with self.note_lock:
            job = self._current_job(record)
            log_path = self.log_path(job.note_name, job.kind)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            journal_file = log_path.open("a+b")
        with journal_file as journal, tempfile.TemporaryDirectory(prefix="spook-codex-") as temporary:
            folder = Path(temporary)
            output = folder / "result.json"
            command = _wrapped(settings.agent.wrapper, timeout) + codex.command(
                settings.programs.codex, job.model, job.effort, output
            )
            if journal.tell():
                journal.write(b"\n")
            journal.write(f"$ {shlex.join(command)}\n(рабочий каталог: {folder})\n\n".encode("utf-8"))
            journal.flush()
            output_start = journal.tell()
            try:
                with self.note_lock:
                    job = self._current_job(record)
                    note = Note(job.note_path)
                    guard = TranscriptGuard.remember(job.note_path)
                    with self._lock:
                        record.snapshot = guard.snapshot
                        self._persist_under_lock()
                    prompt = meeting_input(settings, note, job.skill)
                returncode, stderr_bytes, timed_out = _launch(command, folder, journal, timeout, prompt)
            except (OSError, ValueError) as error:
                result = AgentResult(SummaryState.FAILED, f"Не удалось запустить Codex: {error}")
            else:
                journal.seek(output_start)
                stdout = journal.read().decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                if stderr.strip():
                    journal.write(f"\n--- stderr ---\n{stderr}".encode("utf-8"))
                reason = (
                    f"Превышено время: Агент работал дольше {settings.agent.timeout_minutes:g} мин"
                    if timed_out else codex.failure(stdout, stderr, returncode)
                )
                with self.note_lock:
                    note = Note(self._current_job(record).note_path)
                    warning = guard.restore(note.path)
                    if warning:
                        log.warning("Охрана Транскрипта, %s: %s", note.path.name, warning)
                        journal.write(f"\nОхрана Транскрипта: {warning}\n".encode("utf-8"))
                    result = self._save_reply(note, output, reason, returncode)
                    if result.warning:
                        journal.write(f"\n{result.warning}\n".encode("utf-8"))
                    result = replace(result, warning=" ".join(filter(None, (warning, result.warning))))
            journal.write(_closing_line(result).encode("utf-8"))
        return result

    def _save_reply(self, note: Note, output: Path, reason: str, returncode: int) -> AgentResult:
        if reason:
            return AgentResult(SummaryState.FAILED, reason, returncode)
        try:
            reply = codex.read_reply(output)
            if reply.summary == note.summary():
                raise ValueError("Агент завершился, но Выжимка не обновлена")
            note.set_summary(reply.summary)
        except (OSError, ValueError) as error:
            return AgentResult(SummaryState.FAILED, str(error), returncode)
        warning = self.on_topic(note.path, reply.topic) if reply.topic and not note.has_topic else ""
        return AgentResult(SummaryState.DONE, returncode=returncode, warning=warning)

    def _notify(self, job: AgentJob, result: AgentResult) -> None:
        if result.state is SummaryState.DONE:
            title, body = "Выжимка готова", job.note_name
        else:
            title, body = "Ошибка Агента", f"{job.note_name}: {result.message}"
        send_notification(self._load_settings().programs.notify, title, body)


def _escapes_log_dir(note_name: str) -> bool:
    return not note_name or Path(note_name).name != note_name or note_name in (".", "..")




def _wrapped(wrapper: str, timeout: float) -> list[str]:
    return shlex.split(wrapper.replace("{timeout}", str(round(timeout)))) if wrapper.strip() else []


def _launch(command: list[str], cwd: Path, journal: BinaryIO, timeout: float, prompt: str) -> tuple[int, bytes, bool]:
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=cwd, env=codex.environment(cwd), stdin=subprocess.PIPE, stdout=journal,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        _, stderr = process.communicate(input=prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGTERM)
        try:
            _, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            _, stderr = process.communicate()
        return process.returncode, stderr, True
    wrapper_timed_out = process.returncode != 0 and time.monotonic() - started >= timeout
    return process.returncode, stderr, wrapper_timed_out


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass




def _closing_line(result: AgentResult) -> str:
    code = "" if result.returncode is None else f"Код возврата: {result.returncode}. "
    reason = f" — {result.message}" if result.message else ""
    return f"\n{code}Выжимка: {result.state.label}{reason}\n"
