from __future__ import annotations

import json
import logging
import os
import re
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
from typing import Callable, TextIO
from uuid import uuid4

from spook import codex
from spook.meeting_context import meeting_input
from spook.note import Note, TranscriptGuard
from spook.notify import send_notification
from spook.settings import KindSettings, SessionKind, Settings, codex_model_and_effort

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
        return Note(self.note_path).name


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
    snapshot_id: str | None = None

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
            "snapshot_id": self.snapshot_id,
        }

    @classmethod
    def from_json(cls, data: dict) -> _Record:
        model, effort = codex_model_and_effort(data["model"], data["effort"])
        job = AgentJob(
            note_path=Path(data["note"]),
            kind=SessionKind(data["kind"]),
            skill=data["skill"],
            model=model,
            effort=effort,
        )
        snapshot_id = data.get("snapshot_id")
        return cls(job, SummaryState(data["state"]), snapshot_id=(
            snapshot_id if isinstance(snapshot_id, str) and re.fullmatch("[0-9a-f]{32}", snapshot_id) else None
        ))


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
            saved = [_Record.from_json(item) for item in items if item["kind"] == SessionKind.MEETING and Path(item["note"]).suffix == ".html"]
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
        warning = ""
        if record.snapshot_id:
            try:
                path = TranscriptGuard.snapshot_path(job.note_path, record.snapshot_id)
                warning = TranscriptGuard.recover(job.note_path, path)
                _remove_agent_files(path)
            except (ValueError, OSError):
                warning = "Не удалось восстановить Транскрипт по снимку в Wisp."
        result = AgentResult(SummaryState.FAILED, INTERRUPTED, warning=warning)
        log.warning("Агент закончил: %s — %s: %s", job.note_name, result.state.label, INTERRUPTED)
        try:
            journal_path = self.log_path(job.note_path.stem, job.kind)
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with _open_journal(journal_path) as journal:
                if warning:
                    journal.write(f"\nОхрана Транскрипта: {warning}\n")
                journal.write(_closing_line(result))
        except OSError:
            log.exception("Журнал Агента %s не дописан", job.note_name)
        with self._lock:
            self._records.append(replace(record, state=result.state, message=result.message, warning=warning, snapshot_id=None))
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
        if _escapes_log_dir(note_name):
            raise ValueError("Недопустимое имя Журнала Агента")
        return self._log_dir / kind.value / f"{note_name}.log"

    def _existing_log(self, note_name: str, kind: SessionKind | None) -> Path | None:
        if _escapes_log_dir(note_name):
            return None
        kinds = [kind] if kind is not None else list(SessionKind)
        legacy_log = self._log_dir / f"{note_name}.log"
        for path in [*(self.log_path(note_name, each) for each in kinds), legacy_log]:
            if path.is_file() and not path.is_symlink():
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
            record.snapshot_id = uuid4().hex if state is SummaryState.RUNNING else None
            self._persist_under_lock()
        self.on_change()

    def _current_job(self, record: _Record) -> AgentJob:
        with self._lock:
            return record.job

    def _run(self, record: _Record) -> AgentResult:
        settings = self._load_settings()
        timeout = settings.agent.timeout_minutes * 60
        job = self._current_job(record)
        assert record.snapshot_id is not None
        snapshot = TranscriptGuard.snapshot_path(job.note_path, record.snapshot_id)
        output = snapshot.with_suffix(".json")
        log_path = self.log_path(job.note_path.stem, job.kind)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with _open_journal(log_path) as journal, tempfile.TemporaryDirectory(prefix="spook-codex-") as temporary:
            folder = Path(temporary)
            command = _wrapped(settings.agent.wrapper, timeout) + codex.command(
                settings.programs.codex, job.model, job.effort, output
            )
            if journal.tell():
                journal.write("\n")
            journal.write(f"$ {shlex.join(command)}\n(рабочий каталог: {folder})\n\n")
            journal.flush()
            try:
                with self.note_lock:
                    job = self._current_job(record)
                    guard = TranscriptGuard.remember(job.note_path, snapshot)
                    prompt = meeting_input(settings, Note(job.note_path), job.skill)
                returncode, reason, timed_out = _launch(command, folder, timeout, prompt, journal)
                if timed_out:
                    reason = f"Превышено время: Агент работал дольше {settings.agent.timeout_minutes:g} мин"
                with self.note_lock:
                    note = Note(self._current_job(record).note_path)
                    warning = guard.restore(note.path)
                    if warning:
                        log.warning("Охрана Транскрипта, %s: %s", note.path.name, warning)
                        journal.write(f"\nОхрана Транскрипта: {warning}\n")
                    result = self._save_reply(note, output, reason, returncode)
                    if result.warning:
                        journal.write("\nНе удалось применить Тему из ответа Codex.\n")
                    result = replace(result, warning=" ".join(filter(None, (warning, result.warning))))
            except (OSError, ValueError) as error:
                result = AgentResult(SummaryState.FAILED, f"Не удалось запустить Codex: {error}")
            finally:
                _remove_agent_files(snapshot)
            journal.write(_closing_line(result))
        return result

    def _save_reply(self, note: Note, output: Path, reason: str, returncode: int) -> AgentResult:
        if reason:
            return AgentResult(SummaryState.FAILED, reason, returncode)
        try:
            reply = codex.read_reply(output)
        except ValueError as error:
            return AgentResult(SummaryState.FAILED, str(error), returncode)
        except OSError:
            return AgentResult(SummaryState.FAILED, "Не удалось прочитать ответ Codex", returncode)
        try:
            if note.same_summary(reply.summary):
                return AgentResult(SummaryState.FAILED, "Агент завершился, но Выжимка не обновлена", returncode)
            note.set_summary(reply.summary)
        except (OSError, ValueError):
            return AgentResult(SummaryState.FAILED, "Не удалось сохранить Выжимку в Wisp", returncode)
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


def _open_journal(path: Path) -> TextIO:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def _remove_agent_files(snapshot: Path) -> None:
    snapshot.unlink(missing_ok=True)
    snapshot.with_suffix(".json").unlink(missing_ok=True)


def _launch(command: list[str], cwd: Path, timeout: float, prompt: str, journal: TextIO) -> tuple[int, str, bool]:
    started = time.monotonic()
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stdout, tempfile.TemporaryFile(
        "w+", encoding="utf-8", errors="replace"
    ) as stderr:
        process = subprocess.Popen(
            command, cwd=cwd, env=codex.environment(cwd), stdin=subprocess.PIPE,
            stdout=stdout, stderr=stderr, start_new_session=True,
        )
        try:
            process.communicate(input=prompt.encode("utf-8"), timeout=timeout)
            timed_out = process.returncode != 0 and time.monotonic() - started >= timeout
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_group(process, signal.SIGTERM)
            try:
                process.communicate(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _signal_group(process, signal.SIGKILL)
                process.communicate()
        stdout.seek(0)
        stderr.seek(0)
        return process.returncode, codex.record_output(stdout, stderr, process.returncode, journal), timed_out


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass




def _closing_line(result: AgentResult) -> str:
    code = "" if result.returncode is None else f"Код возврата: {result.returncode}. "
    reason = f" — {result.message}" if result.message else ""
    return f"\n{code}Выжимка: {result.state.label}{reason}\n"
