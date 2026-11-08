"""Агент: очередь Заметок и фоновый запуск Claude Code по Скиллу (ADR-0003).

После завершения Сессии Заметка встаёт в очередь; Агенты работают по одному, в порядке очереди.
Запуск — сама программа Claude Code по полному пути из `[programs].claude` (не обёртка из оболочки
пользователя), по итогам пробы (`docs/research/headless-skill-probe.md`): `-p "/<скилл> <путь>"`,
`--model`, `--effort` из настроек вида, поток событий `stream-json`, режим прав `default`, stdin —
/dev/null (`AgentJob.command`); запреты `--disallowedTools` (`denied_rules`) закрывают правку `.claude/`
и инструментов Вики, что бы ни разрешали Скилл и настройки. Агенту Встречи ещё передаются названия заметок Хранилищ и путь
к Словарю терминов (`spook.meeting_context`). Рабочий каталог — папка вида, у Лекции — корень Вики.
Запуск можно обернуть командой `[agent].wrapper` (например, `systemd-run --user …` ради чистого
окружения); дольше `[agent].timeout_minutes` он не идёт. Весь вывод как есть пишется в журнал
`<каталог состояния>/agent/<вид>/<имя Заметки>.log` (вне Хранилищ); перезапуск дописывает его ниже
прежнего запуска. Начало и итог запуска попадают и в журнал службы. Об итоге сообщает программа
`[programs].notify`. Автоматических повторов нет. Очередь переживает перезапуск службы
(`AgentQueue.restore`): ждавшие Заметки снова встают в очередь, прерванный запуск — «ошибка».

Состояния Выжимки: «нет», «в очереди», «работает», «готово», «ошибка». «Готово» — вывод не говорит
об ошибке и над заголовком «Транскрипт» появилась Выжимка; всё остальное — «ошибка» с причиной:
«Превышено время», причины из вывода (`spook.claude_output`: хук/VPN, лимит, сеть, отказ в правах)
или сообщение запуска.

Предупреждения `AgentResult.warning` (видны на странице и в журнале, состояние Выжимки от них не
зависит): `model`/`effort` в шапке Скилла перебивают выбор в настройках; отказы в правах при
удачном запуске; охрана Транскрипта (`TranscriptGuard`): перед запуском запоминается всё от
заголовка «Транскрипт» до конца Заметки, после запуска сверяется; если Агент тронул этот текст,
стёр заголовок или всю Заметку, исходный Транскрипт возвращается на место (Выжимка сохраняется).

Перезапуск с другой моделью — `enqueue` с готовым `AgentJob`. Если Заметку во время запуска
переименует команда Темы, сверять Транскрипт нужно по новому пути — `TranscriptGuard.restore`
принимает путь.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from queue import Queue
from typing import BinaryIO, Callable

from spook.claude_output import read_output
from spook.meeting_context import write_meeting_context
from spook.note import Note, TranscriptGuard
from spook.notify import send_notification
from spook.settings import KindSettings, SessionKind, Settings

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
    """Один запуск Агента: что, чем и где."""

    note_path: Path
    kind: SessionKind
    skill: str
    model: str
    effort: str
    cwd: Path

    @classmethod
    def for_note(cls, note_path: Path, kind: SessionKind, settings: KindSettings) -> AgentJob:
        """Запуск по настройкам вида: Скилл, модель, effort; рабочий каталог — папка вида
        (у Лекции — корень Вики, см. `Settings.for_kind`)."""
        return cls(
            note_path=note_path,
            kind=kind,
            skill=settings.skill,
            model=settings.model,
            effort=settings.effort,
            cwd=settings.agent_cwd or settings.folder,
        )

    @property
    def note_name(self) -> str:
        return self.note_path.stem

    def command(self, program: str, denied: list[str] | None = None) -> list[str]:
        """Запуск без диалога по итогам пробы (`docs/research/headless-skill-probe.md`): поток
        событий `stream-json` (без `--verbose` он не работает) с событиями хуков — это и есть журнал;
        режим прав `default` — всё, что не разрешено Скиллом и настройками, отклоняется, а
        `--permission-prompts none` прямо говорит Агенту, что подтверждать некому. `denied` —
        правила `--disallowedTools` (`denied_rules`): запрет сильнее любого разрешения."""
        return [
            program,
            "-p", f"/{self.skill} {self.note_path}",
            "--model", self.model,
            "--effort", self.effort,
            "--output-format", "stream-json", "--verbose", "--include-hook-events",
            "--permission-mode", "default", "--permission-prompts", "none",
            *(["--disallowedTools", *denied] if denied else []),
        ]


def denied_rules(settings: Settings, cwd: Path) -> list[str]:
    """Запреты для каждого запуска Агента, сильнее разрешений Скилла и настроек пользователя.
    Транскрипт — недоверенный ввод, поэтому Агенту закрыты пути к выполнению кода в следующих запусках:

    - `.claude/` на любой глубине в рабочем каталоге, папках видов и Вики — там проектные настройки,
      хуки и Скиллы, которые Claude Code подхватит при следующем запуске;
    - `_tools/` и `_templates/` Вики — иначе Агент переписал бы `wiki_html.py`, а затем запустил его
      разрешённой командой.

    Форма правил — по документации Claude Code (Permissions, «Read and Edit»): `//` — абсолютный путь
    от корня, шаблоны как в gitignore, правило `Edit` действует на все встроенные инструменты записи."""
    folders: list[Path] = []
    for folder in (cwd, settings.meeting.folder, settings.lecture.folder, settings.wiki):
        if folder not in folders:
            folders.append(folder)
    return [
        *(f"Edit(/{folder}/**/.claude/**)" for folder in folders),
        f"Edit(/{settings.wiki}/_tools/**)",
        f"Edit(/{settings.wiki}/_templates/**)",
    ]


@dataclass(frozen=True)
class AgentResult:
    """Итог запуска: «готово» или «ошибка» с причиной для пользователя.

    `warning` — предупреждение, не меняющее состояния: например, что Транскрипт пришлось вернуть.
    """

    state: SummaryState
    message: str = ""
    returncode: int | None = None
    warning: str = ""


@dataclass(frozen=True)
class AgentJobView:
    """Снимок запуска для страниц."""

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
    snapshot: str | None = None  # текст Заметки до запуска — по нему сверяется Транскрипт

    def view(self) -> AgentJobView:
        return AgentJobView(self.job, self.state, self.message, self.warning)

    def to_json(self) -> dict[str, object]:
        return {
            "note": str(self.job.note_path),
            "kind": self.job.kind.value,
            "skill": self.job.skill,
            "model": self.job.model,
            "effort": self.job.effort,
            "cwd": str(self.job.cwd),
            "state": self.state.value,
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_json(cls, data: dict) -> _Record:
        job = AgentJob(
            note_path=Path(data["note"]),
            kind=SessionKind(data["kind"]),
            skill=data["skill"],
            model=data["model"],
            effort=data["effort"],
            cwd=Path(data["cwd"]),
        )
        return cls(job, SummaryState(data["state"]), snapshot=data.get("snapshot"))


INTERRUPTED = "Агент прерван перезапуском службы"
QUEUE_NAME = "queue.json"


class AgentQueue:
    """Очередь Агента: FIFO, один запуск за раз, в своём потоке.

    `on_change` вызывается при каждой смене состояния любого запуска (в том числе при постановке
    в очередь) — вне замка очереди, из потока вызывающего или из потока Агента.

    Очередь переживает перезапуск службы: ждущие и идущий запуски держатся в
    `<каталог журналов>/queue.json` (у идущего — ещё текст Заметки до запуска, без звука), и `restore`
    при запуске службы снова ставит ждущие в очередь, а идущий завершает «ошибкой» `INTERRUPTED`,
    сверив Транскрипт по сохранённому тексту.
    """

    def __init__(self, load_settings: Callable[[], Settings], log_dir: Path) -> None:
        self._load_settings = load_settings
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._records: list[_Record] = []
        self._pending: Queue[_Record] = Queue()
        self._worker: threading.Thread | None = None
        self.on_change: Callable[[], None] = lambda: None

    def enqueue(self, job: AgentJob) -> None:
        self._enqueue(_Record(job))

    def _enqueue(self, record: _Record) -> None:
        with self._lock:
            self._records.append(record)
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="spook-agent", daemon=True)
                self._worker.start()
            self._persist()
        self._pending.put(record)
        self.on_change()

    def restore(self) -> None:
        """При запуске службы: очередь прошлой работы службы. Ждавшие Заметки снова встают в очередь
        в прежнем порядке; запуск, который прервал перерыв, — «ошибка» `INTERRUPTED` с уведомлением,
        а Транскрипт его Заметки сверяется по тексту, запомненному до запуска."""
        path = self._log_dir / QUEUE_NAME
        try:
            saved = [_Record.from_json(item) for item in json.loads(path.read_text(encoding="utf-8"))]
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Очередь Агента в %s не прочитана — удалена", path)
            path.unlink(missing_ok=True)
            return
        for record in saved:
            if record.state is SummaryState.RUNNING:
                self._interrupted(record)  # Заметку, стёртую Агентом, вернёт снимок
            elif not record.job.note_path.is_file():
                log.warning("Заметки %s больше нет — из очереди Агента убрана", record.job.note_path)
            else:
                self._enqueue(_Record(record.job))
        with self._lock:
            self._persist()

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

    def _persist(self) -> None:
        """Ждущие и идущий запуски — на диск; под замком, при каждой смене. Пишется атомарно."""
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
        """Недавние запуски, новые первыми; у перезапущенной Заметки — только последний запуск."""
        with self._lock:
            latest: dict[Path, AgentJobView] = {}
            for record in reversed(self._records):
                latest.setdefault(record.job.note_path, record.view())
            return tuple(latest.values())[:RECENT_JOBS]

    def job_for(self, note_path: Path) -> AgentJobView | None:
        """Последний запуск этой Заметки в нынешней работе службы; `None` — запусков не было."""
        with self._lock:
            for record in reversed(self._records):
                if record.job.note_path == note_path:
                    return record.view()
        return None

    def note_renamed(self, old_path: Path, new_path: Path, kind: SessionKind) -> None:
        """Заметка вида `kind` получила имя по Теме от Агента: её запуски и журнал переходят на новое имя.

        Журнал идущего запуска переименовывается на ходу — открытый файл это переживает.
        """
        with self._lock:
            for record in self._records:
                if record.job.note_path == old_path:
                    record.job = replace(record.job, note_path=new_path)
            old_log = self._existing_log(old_path.stem, kind)
            new_log = self.log_path(new_path.stem, kind)
            if old_log is not None and old_log != new_log:
                old_log.replace(new_log)
            self._persist()
        self.on_change()

    def log_path(self, note_name: str, kind: SessionKind) -> Path:
        """Журнал Заметки: `<каталог журналов>/<вид>/<имя Заметки>.log` — у Встречи и Лекции
        с одинаковым именем журналы разные."""
        return self._log_dir / kind.value / f"{note_name}.log"

    def _existing_log(self, note_name: str, kind: SessionKind | None) -> Path | None:
        """Журнал, который уже есть: по виду, а если вид не назван — первый найденный; затем журнал
        прежнего вида `<каталог журналов>/<имя Заметки>.log`, без вида. `None` — журнала нет или имя
        ведёт из каталога журналов."""
        if not note_name or Path(note_name).name != note_name or note_name in (".", ".."):
            return None
        kinds = [kind] if kind is not None else list(SessionKind)
        for path in [*(self.log_path(note_name, each) for each in kinds), self._log_dir / f"{note_name}.log"]:
            if path.is_file():
                return path
        return None

    def has_log(self, note_name: str, kind: SessionKind) -> bool:
        return self._existing_log(note_name, kind) is not None

    def read_log(self, note_name: str, kind: SessionKind | None = None) -> str | None:
        """Журнал запуска по имени Заметки и виду; без вида — по одному имени, как в прежних адресах.
        `None`, если журнала нет или имя ведёт из каталога журналов."""
        path = self._existing_log(note_name, kind)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # --- поток Агента -------------------------------------------------------------

    def _work(self) -> None:
        while True:
            record = self._pending.get()
            self._set(record, SummaryState.RUNNING)
            job = self._job(record)
            log.info("Агент запущен: %s — %s, %s, %s", job.note_name, job.skill, job.model, job.effort)
            try:
                result = self._run(record)
            except Exception as error:  # очередь должна пережить любой сбой одного запуска
                log.exception("Запуск Агента для %s сорвался", record.job.note_name)
                result = AgentResult(SummaryState.FAILED, f"Сбой запуска: {error}")
            self._set(record, result.state, result.message, result.warning)
            job = self._job(record)  # Агент мог переименовать Заметку по Теме
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
            self._persist()
        self.on_change()

    def _job(self, record: _Record) -> AgentJob:
        """Текущий запуск записи: Агент мог переименовать Заметку по ходу работы (`note_renamed`)."""
        with self._lock:
            return record.job

    def _run(self, record: _Record) -> AgentResult:
        """Запускает программу; stdout — в журнал по ходу, stderr — в журнал отдельным блоком после.
        Транскрипт под охраной: что Агент с ним сделал, выясняется и исправляется до оценки итога.

        Журнал и Заметка по ходу запуска могут сменить имя (Тема от Агента), поэтому вывод читается
        через открытый файл, а охрана и оценка итога идут по текущему пути Заметки.
        """
        settings = self._load_settings()
        program = settings.programs.claude
        timeout = settings.agent.timeout_minutes * 60
        job = self._job(record)
        command = _wrapped(settings.agent.wrapper, timeout) + job.command(program, denied_rules(settings, job.cwd))
        if job.kind is SessionKind.MEETING:  # названия заметок для ссылок и Словарь терминов
            context = write_meeting_context(settings, job.note_path, self._log_dir / "meeting-context.md")
            command += ["--append-system-prompt-file", str(context)]
        guard = TranscriptGuard.remember(job.note_path)
        with self._lock:  # если работу прервёт перезапуск службы, Транскрипт сверят по этому снимку
            record.snapshot = guard.snapshot
            self._persist()
        summary_before = Note(job.note_path).summary()
        skill_warning = _skill_header_warning(job)
        log_path = self.log_path(job.note_name, job.kind)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Перезапуск дописывает журнал Заметки: прежний запуск остаётся выше нового.
        with log_path.open("a+b") as journal:
            if journal.tell():
                journal.write(b"\n")
            journal.write(f"$ {shlex.join(command)}\n(рабочий каталог: {job.cwd})\n\n".encode("utf-8"))
            if skill_warning:
                journal.write(f"{skill_warning}\n\n".encode("utf-8"))
            journal.flush()
            output_start = journal.tell()
            try:
                returncode, stderr_bytes, timed_out = _launch(command, job.cwd, journal, timeout)
            except OSError as error:
                result = AgentResult(SummaryState.FAILED, f"Не удалось запустить {program}: {error.strerror or error}")
            else:
                journal.seek(output_start)
                stdout = journal.read().decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                if stderr.strip():
                    journal.write(f"\n--- stderr ---\n{stderr}".encode("utf-8"))
                job = self._job(record)  # Заметка могла получить имя по Теме во время запуска
                warning = guard.restore(job.note_path)
                if warning:
                    log.warning("Охрана Транскрипта, %s: %s", job.note_name, warning)
                    journal.write(f"\nОхрана Транскрипта: {warning}\n".encode("utf-8"))
                if timed_out:
                    minutes = f"{settings.agent.timeout_minutes:g}"
                    judged = AgentResult(SummaryState.FAILED, f"Превышено время: Агент работал дольше {minutes} мин", returncode)
                else:
                    judged = self._judge(job, returncode, stdout, stderr, summary_before)
                if judged.warning:
                    journal.write(f"\n{judged.warning}\n".encode("utf-8"))
                result = replace(judged, warning=_joined(skill_warning, judged.warning, warning))
            journal.write(_closing_line(result).encode("utf-8"))
        return result

    def _judge(self, job: AgentJob, returncode: int, stdout: str, stderr: str, summary_before: str) -> AgentResult:
        """Итог запуска: причину ошибки даёт вывод (`claude_output`), Выжимку — сама Заметка: по выводу
        завершение без Выжимки от успеха не отличить. «Готово» — Выжимка есть и отличается от той,
        что была до запуска (при перезапуске прежняя Выжимка не в счёт). Отказы в правах —
        предупреждение, а если Выжимки нет — причина ошибки."""
        verdict = read_output(stdout, stderr, returncode)
        if verdict.failure:
            return AgentResult(SummaryState.FAILED, verdict.failure, returncode, warning=verdict.denials)
        try:
            summary = Note(job.note_path).summary()
        except OSError as error:
            return AgentResult(SummaryState.FAILED, f"Заметка не прочитана: {error.strerror or error}", returncode)
        if not summary:
            return AgentResult(SummaryState.FAILED, verdict.denials or "Агент завершился, но Выжимки в Заметке нет", returncode)
        if summary == summary_before:
            return AgentResult(SummaryState.FAILED, verdict.denials or "Агент завершился, но Выжимка не обновлена", returncode)
        return AgentResult(SummaryState.DONE, returncode=returncode, warning=verdict.denials)

    def _notify(self, job: AgentJob, result: AgentResult) -> None:
        """Системное уведомление об итоге — программой `[programs].notify`: заголовок и текст."""
        if result.state is SummaryState.DONE:
            title, body = "Выжимка готова", job.note_name
        else:
            title, body = "Ошибка Агента", f"{job.note_name}: {result.message}"
        send_notification(self._load_settings().programs.notify, title, body)


def _skill_header_warning(job: AgentJob) -> str:
    """Предупреждение, если в шапке Скилла есть `model` или `effort`: они перебивают флаги запуска
    (проба, № 06). Скилл ищется так же, как его найдёт Claude Code: сначала пользовательский
    (`$CLAUDE_CONFIG_DIR` или `~/.claude`), он важнее; затем проектный `<рабочий каталог>/.claude`."""
    user_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    for skills_dir in (user_dir / "skills", job.cwd / ".claude" / "skills"):
        try:
            text = (skills_dir / job.skill / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            continue
        keys = _header_keys(text)
        named = ", ".join(key for key in ("model", "effort") if key in keys)
        return f"В шапке Скилла {job.skill} указаны {named} — они перебивают модель и effort из настроек Spook." if named else ""
    return ""


def _header_keys(text: str) -> set[str]:
    """Ключи верхнего уровня в шапке (между первыми строками `---`)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return set()
    keys = set()
    for line in lines[1:]:
        if line.strip() == "---":
            return keys
        if line[:1].strip() and ":" in line:
            keys.add(line.split(":", 1)[0].strip())
    return set()


def _wrapped(wrapper: str, timeout: float) -> list[str]:
    """Команда-обёртка запуска из настроек; `{timeout}` в ней — таймаут в секундах."""
    return shlex.split(wrapper.replace("{timeout}", str(round(timeout)))) if wrapper.strip() else []


def _launch(command: list[str], cwd: Path, journal: BinaryIO, timeout: float) -> tuple[int, bytes, bool]:
    """Запускает команду: stdin — /dev/null, stdout — в журнал, stderr собирается. Возвращает код
    возврата, stderr и признак «превышено время».

    По истечении таймаута вся группа процессов получает SIGTERM, через `KILL_GRACE_SECONDS` — SIGKILL.
    Обёртка `systemd-run --wait` от этого прерывается, но её юнит живёт дальше — у него свой предел
    (`-p RuntimeMaxSec={timeout}`); такой запуск, завершённый с ошибкой не раньше таймаута, тоже
    считается превысившим время.
    """
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGTERM)
        try:
            _, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            _, stderr = process.communicate()
        return process.returncode, stderr, True
    timed_out = process.returncode != 0 and time.monotonic() - started >= timeout
    return process.returncode, stderr, timed_out


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _joined(*warnings: str) -> str:
    return " ".join(warning for warning in warnings if warning)


def _closing_line(result: AgentResult) -> str:
    code = "" if result.returncode is None else f"Код возврата: {result.returncode}. "
    reason = f" — {result.message}" if result.message else ""
    return f"\n{code}Выжимка: {result.state.label}{reason}\n"
