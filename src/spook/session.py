"""Сессия: один непрерывный захват от «Старт» до «Стоп».

Состояния: «нет Сессии» → «идёт» ⇄ «на Паузе» → «завершается» → «нет Сессии». На Паузе захват
остановлен; захваченное время — сумма интервалов «идёт», Паузы в него не входят. Сессия живёт
в службе, а не во вкладке: страницы получают полное состояние при подключении и дальше изменения.
После завершения Заметка с Репликами встаёт в очередь Агента; Сессия без Реплик Агента
не запускает, её пустая Заметка удаляется.

Встреча захватывает Системный звук и Микрофон, Лекция — только Системный звук. Очередь распознавания
одна на оба Источника и не ограничена: если распознаватель не успевает, звук ждёт в памяти,
а отставание — длительность отрезков в очереди и распознаваемого сейчас — сообщается страницам.
Реплики пишутся по времени начала: распознанная Реплика придерживается, пока другой Источник
ещё может дать Реплику, начавшуюся раньше (`_SourceRun.frontier`).

Автостоп: идущая Сессия без речи дольше времени из настроек завершается тем же путём, что по «Стоп»,
и об этом уходит системное уведомление. «Без речи» считается по Репликам: отсчёт идёт от момента
распознавания последней непустой Реплики любого Источника, а в начале — от «Старт» или «Продолжить».
Проверка — таймер часов службы на момент, когда тишина достигнет заданного времени; он не зависит
от входящего звука. На Паузе таймера нет, после «Продолжить» отсчёт начинается заново.

Перерыв в работе службы (сон ноутбука, сбой, перезапуск) считается тишиной. Пока служба работает
с Сессией (идёт или на Паузе), она раз в `ACTIVITY_SECONDS` отмечает время последней активности
и держит сведения о Сессии на диске (`session_record`, без звука и текста Реплик). Перерыв — от
последней активности до момента, когда служба снова его видит: при запуске (`restore`) или, без
перезапуска, по скачку часов дольше `BREAK_SECONDS` (проверяют таймер активности, проверка
автостопа и пришедший звук — что раньше). Перерыв считается тишиной: у идущей Сессии с временем
автостопа сравнивается тишина целиком — от последней Реплики (или «Старт», «Продолжить») до
возвращения службы, вместе с перерывом. Короче: идущая Сессия продолжается в ту же Заметку
с отметкой `*Пропуск …*` (вид времени — как у отметки о Паузе), захват после перезапуска начинается
заново, а отсчёт тишины продолжается от последней Реплики; Сессия на Паузе остаётся на Паузе
(с временем автостопа сравнивается сам перерыв), отдельной отметки нет — перерыв войдёт в Паузу.
Дольше — Сессия завершается, как при автостопе, но время конца — последняя активность. Об итоге —
системное уведомление.
"""

from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
from bisect import insort
from collections import deque
from dataclasses import dataclass, field, replace
from functools import partial
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from spook.agent import AgentJob, AgentJobView, AgentQueue
from spook.audio import SAMPLE_RATE, AudioInput, AudioSegment, Capture, CaptureUnavailable, DeviceChange, Level, Source
from spook.clock import Clock, Timer
from spook.cutting import SpeechDetectorFactory, UtteranceCutter
from spook.note import Note, NoteNotFound, clean_link, clean_topic, find_session_note

from spook.notify import send_notification
from spook.recognizer import Recognizer
from spook.session_record import SessionRecord, SessionState, SessionStore
from spook.settings import SessionKind, Settings
from spook.transcript import DeviceMark, Entry, Mark, Paragraph, PauseMark, Transcript, Utterance, format_offset

log = logging.getLogger(__name__)

NO_DEVICE = "нет устройства"  # название на странице, пока у Источника нет устройства для захвата


class CommandRejected(Exception):
    """Команда отклонена; текст — понятная пользователю причина."""


@dataclass(frozen=True)
class SourceView:
    """Источник идущей Сессии и устройство, с которого он захватывается."""

    source: Source
    device_name: str


@dataclass(frozen=True)
class SessionView:
    """Снимок состояния для страницы: Сессия и недавние запуски Агента."""

    state: SessionState
    kind: SessionKind | None = None
    started_at: datetime | None = None
    elapsed_seconds: int = 0
    note_path: Path | None = None
    topic: str = ""
    link: str = ""  # Ссылка Лекции на видео или страницу доклада
    entries: tuple[Entry, ...] = ()  # абзацы и отметки Транскрипта по порядку
    sources: tuple[SourceView, ...] = ()
    warnings: tuple[str, ...] = ()  # о пропавших устройствах; живут до конца Сессии
    backlog_seconds: float = 0.0  # отставание: звук в очереди распознавания и распознаваемый сейчас
    backlog_segments: int = 0  # отрезков в очереди, включая распознаваемый сейчас
    agent: tuple[AgentJobView, ...] = ()  # недавние запуски Агента, новые первыми

    @property
    def elapsed_label(self) -> str:
        return format_offset(self.elapsed_seconds)

    @property
    def backlog_label(self) -> str:
        """Отставание целыми секундами, с округлением вверх: «6 с»."""
        return f"{math.ceil(self.backlog_seconds)} с"

    @property
    def backlog_visible(self) -> bool:
        """Отставание показывается, пока за распознаваемым сейчас отрезком ждёт хотя бы один:
        иначе предупреждение мигало бы на каждой Реплике, когда распознаватель успевает.
        В «завершается» остаток виден, пока очередь не пуста."""
        if self.state is SessionState.FINISHING:
            return self.backlog_seconds > 0
        return self.backlog_segments >= 2


@dataclass(frozen=True)
class StateChanged:
    view: SessionView


@dataclass(frozen=True)
class ParagraphAdded:
    paragraph: Paragraph


@dataclass(frozen=True)
class ParagraphUpdated:
    """Последний абзац продолжен Репликой: страница заменяет его на месте."""

    paragraph: Paragraph


@dataclass(frozen=True)
class MarkAdded:
    mark: Mark


@dataclass(frozen=True)
class LevelsChanged:
    """Уровень сигнала Источников по последней порции звука; идёт с частотой порций захвата."""

    levels: dict[Source, Level]


@dataclass(frozen=True)
class AgentChanged:
    jobs: tuple[AgentJobView, ...]


@dataclass(frozen=True)
class BacklogChanged:
    """Отставание изменилось: отрезок встал в очередь или распознан."""

    view: SessionView


Event = StateChanged | ParagraphAdded | ParagraphUpdated | MarkAdded | LevelsChanged | AgentChanged | BacklogChanged


@dataclass
class Subscription:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[Event]


_FINISH = object()

ACTIVITY_SECONDS = 30  # как часто обновляется время последней активности, пока служба работает с Сессией
# Без перезапуска перерыв (сон ноутбука) виден как скачок часов: с последней активности прошло больше.
BREAK_SECONDS = 90

SOURCES_BY_KIND = {
    SessionKind.MEETING: (Source.SYSTEM, Source.MIC),
    SessionKind.LECTURE: (Source.SYSTEM,),
}


@dataclass
class _SourceRun:
    """Захват и нарезка одного Источника внутри Сессии."""

    cutter: UtteranceCutter
    capture: Capture | None = None
    queued: deque[float] = field(default_factory=deque)  # начала Реплик, ждущих распознавания, в порядке очереди

    def frontier(self) -> float:
        """Раньше этого времени ни одна ещё не распознанная Реплика Источника не начнётся:
        начало первой Реплики в очереди, иначе начало накопленного звука; когда захват
        остановлен и остаток ушёл в очередь, Источник не даст уже ничего."""
        if self.queued:
            return self.queued[0]
        if self.capture is not None or self.cutter.buffered_seconds > 0:
            return self.cutter.position
        return math.inf


@dataclass
class _Run:
    """Всё, что живёт ровно одну Сессию."""

    kind: SessionKind
    started_at: datetime
    note: Note
    sources: dict[Source, _SourceRun]
    auto_stop: timedelta  # время автостопа из настроек на момент «Старт»
    last_speech_at: datetime  # по часам службы: распознана непустая Реплика, а до того — «Старт» или «Продолжить»
    auto_stop_timer: Timer | None = None  # проверка автостопа; есть только в состоянии «идёт»
    alive_at: datetime = field(init=False)  # последняя активность: служба работала с Сессией (идёт или на Паузе)
    activity_timer: Timer | None = None  # обновление `alive_at`; есть в «идёт» и «на Паузе»
    topic: str = ""
    named_topic: str = ""  # Тема в имени Заметки; имя меняется один раз после «Стоп», если Тема другая
    link: str = ""
    transcript: Transcript = field(init=False)
    work: queue.Queue = field(default_factory=queue.Queue)
    held: list[Utterance] = field(default_factory=list)  # распознанные Реплики по времени начала, ждущие более ранних
    devices: dict[Source, str] = field(default_factory=dict)  # название устройства каждого Источника Сессии
    pinned: dict[Source, str] = field(default_factory=dict)  # закреплённые устройства из настроек на момент «Старт»
    warnings: dict[Source, str] = field(default_factory=dict)  # последнее предупреждение о пропаже по Источнику
    running_since: datetime | None = None  # начало текущего интервала «идёт»; None на Паузе и после «Стоп»
    captured_before: float = 0.0  # секунды закрытых интервалов «идёт»
    intervals: list[tuple[float, datetime]] = field(default_factory=list)  # начала интервалов «идёт»: (захваченное время, момент по часам)
    paused_at: datetime | None = None
    backlog_samples: int = 0  # отставание в отсчётах звука (целые — без ошибок округления)
    backlog_segments: int = 0  # отрезков в очереди, включая распознаваемый сейчас
    ended_at: datetime | None = None  # время конца, если это не момент, когда очередь доработана
    # Захваченное время, с которого после перезапуска службы считает нарезка: её время снова идёт с нуля.
    capture_base: float = 0.0

    def __post_init__(self) -> None:
        self.alive_at = self.started_at
        if self.kind is SessionKind.MEETING:
            self.transcript = Transcript(time_label=self.clock_label, speakers=True)
        else:
            self.transcript = Transcript(time_label=self.offset_label)  # говорящий не указывается

    def open_interval(self, now: datetime) -> None:
        """Начало интервала «идёт» — при «Старт» и «Продолжить»."""
        self.running_since = now
        self.intervals.append((self.captured_before, now))

    def close_interval(self, now: datetime) -> None:
        self.captured_before = self.captured_seconds(now)
        self.running_since = None

    def captured_seconds(self, now: datetime) -> float:
        """Захваченное время — сумма интервалов «идёт»; Паузы в него не входят."""
        if self.running_since is None:
            return self.captured_before
        return self.captured_before + (now - self.running_since).total_seconds()

    def clock_at(self, offset: float) -> datetime:
        """Момент по часам, когда захватывалось время `offset`: Паузы раздвигают интервалы «идёт»."""
        for position, moment in reversed(self.intervals):
            if offset >= position:
                return moment + timedelta(seconds=offset - position)
        return self.started_at + timedelta(seconds=offset)

    def clock_label(self, offset: float) -> str:
        """Время по часам `ЧЧ:ММ` для времени нарезки `offset`."""
        return f"{self.clock_at(self.capture_base + offset):%H:%M}"

    def offset_label(self, offset: float) -> str:
        """Время от начала Сессии без учёта Пауз и перерывов для времени нарезки `offset`."""
        return format_offset(self.capture_base + offset)


class SessionService:
    """Машина состояний Сессии; потокобезопасна: команды приходят с страницы, звук — из захвата,
    текст — из потока распознавания."""

    def __init__(
        self,
        load_settings: Callable[[], Settings],
        audio_input: AudioInput,
        recognizer: Recognizer,
        clock: Clock,
        agent: AgentQueue,
        speech_detector: SpeechDetectorFactory,
        record_path: Path | None = None,
    ) -> None:
        """`record_path` — файл сведений об идущей Сессии (`session_record`); без него Сессия
        перерыв в работе службы не переживает."""
        self._load_settings = load_settings
        self._audio_input = audio_input
        self._recognizer = recognizer
        self._speech_detector = speech_detector
        self._clock = clock
        self._agent = agent
        self._lock = threading.RLock()
        self._state = SessionState.NONE
        self._run: _Run | None = None
        self._subscribers: list[Subscription] = []
        self._store = SessionStore(record_path)
        agent.on_change = self._on_agent_change

    # --- команды ---------------------------------------------------------------

    def start(self, kind: SessionKind, topic: str = "", link: str = "") -> SessionView:
        """«Старт»: вид Сессии, необязательная Тема и, для Лекции, необязательная Ссылка."""
        with self._lock:
            if self._state is not SessionState.NONE:
                raise CommandRejected(f"Сессия уже {self._state.label}")
            settings = self._load_settings()
            folder = settings.for_kind(kind).folder
            started_at = self._clock.now()
            topic = clean_topic(topic)
            link = clean_link(link) if kind is SessionKind.LECTURE else ""
            sources = {
                source: _SourceRun(UtteranceCutter(source, settings.recognition, self._speech_detector()))
                for source in SOURCES_BY_KIND[kind]
            }
            try:
                note = Note.create(folder, started_at, kind, topic, link)
            except OSError as error:
                raise CommandRejected(
                    f"Нельзя писать в папку {_folder_name(kind)} {folder}: {error.strerror or error}"
                ) from error
            run = _Run(
                kind=kind,
                started_at=started_at,
                note=note,
                sources=sources,
                auto_stop=timedelta(minutes=settings.auto_stop_minutes),
                last_speech_at=started_at,
                topic=topic,
                named_topic=topic,
                link=link,
                pinned={source: settings.sources.for_source(source) for source in SOURCES_BY_KIND[kind]},
            )
            run.open_interval(started_at)
            try:
                self._start_captures(run)
            except CaptureUnavailable as error:
                self._stop_captures(run)
                note.path.unlink(missing_ok=True)
                raise CommandRejected(str(error)) from error
            self._run = run
            self._state = SessionState.RUNNING
            threading.Thread(
                target=self._recognize_until_finished, args=(run,), name="spook-recognition", daemon=True
            ).start()
            self._schedule_auto_stop(run)
            self._schedule_activity(run)
            self._save(run)
            self._publish(StateChanged(self.view()))
            return self.view()

    def pause(self) -> SessionView:
        with self._lock:
            if self._state is SessionState.NONE:
                raise CommandRejected("Сессии нет")
            if self._state is not SessionState.RUNNING:
                raise CommandRejected(f"Сессия уже {self._state.label}")
            run = self._run
            assert run is not None
            now = self._clock.now()
            run.close_interval(now)
            run.paused_at = now
            self._cancel_auto_stop(run)  # на Паузе отсчёт тишины не идёт
            self._state = SessionState.PAUSED  # звук, пришедший с этого момента, отбрасывается
            self._save(run)
            self._publish(StateChanged(self.view()))
        self._stop_captures(run)
        with self._lock:
            self._flush_cutters(run)  # незакрытые Реплики закрываются и уходят в распознавание
            return self.view()

    def resume(self) -> SessionView:
        with self._lock:
            if self._state is SessionState.NONE:
                raise CommandRejected("Сессии нет")
            if self._state is not SessionState.PAUSED:
                raise CommandRejected(f"Сессия не на Паузе: {self._state.label}")
            run = self._run
            assert run is not None
            try:
                self._start_captures(run)
            except CaptureUnavailable as error:
                self._stop_captures(run)
                raise CommandRejected(str(error)) from error
            now = self._clock.now()
            mark = _pause_mark(run, resumed_at=now)
            run.paused_at = None
            run.open_interval(now)
            run.last_speech_at = now  # отсчёт тишины начинается заново
            self._state = SessionState.RUNNING
            # Отметка идёт через очередь распознавания: так она встаёт после Реплики,
            # закрытой «Паузой», и перед Репликами после «Продолжить».
            run.work.put(mark)
            self._schedule_auto_stop(run)
            self._save(run)
            self._publish(StateChanged(self.view()))
            return self.view()

    def stop(self) -> SessionView:
        with self._lock:
            if self._state is SessionState.NONE:
                raise CommandRejected("Сессии нет")
            if self._state is SessionState.FINISHING:
                raise CommandRejected("Сессия уже завершается")
            run = self._run
            assert run is not None
            self._begin_finishing(run)
        self._finish_capture(run)
        return self.view()

    def set_topic(self, topic: str) -> SessionView:
        """Тема идущей Сессии; в имя Заметки попадёт после «Стоп»."""
        with self._lock:
            run = self._changeable_run()
            run.topic = clean_topic(topic)
            self._save(run)
            self._publish(StateChanged(self.view()))
            return self.view()

    def set_link(self, link: str) -> SessionView:
        """Ссылка идущей Лекции; сразу пишется в свойство `source` Заметки."""
        with self._lock:
            run = self._changeable_run()
            if run.kind is not SessionKind.LECTURE:
                raise CommandRejected("Ссылка — только у Лекции")
            run.link = clean_link(link)
            run.note.set_link(run.link)
            self._save(run)
            self._publish(StateChanged(self.view()))
            return self.view()

    def _changeable_run(self) -> _Run:
        """Сессия, у которой ещё можно менять Тему и Ссылку: до «Стоп»."""
        if self._state is SessionState.NONE:
            raise CommandRejected("Сессии нет")
        if self._state is SessionState.FINISHING:
            raise CommandRejected("Сессия уже завершается")
        assert self._run is not None
        return self._run

    def rename_note(self, note_path: Path, topic: str) -> Path:
        """Тема от Агента (команда `spook topic`): завершённая Заметка без Темы получает имя
        `ГГГГ-ММ-ДД ЧЧ-ММ <Тема>.md`, её запуски Агента и журнал переходят на новое имя.

        Отклоняется, если путь ведёт вне папок Встреч и Лекций из настроек (кроме Заметки последней
        Сессии — её папка могла смениться в настройках во время Сессии), Заметки нет, Тема
        в имени уже есть, Сессия с этой Заметкой ещё идёт или Тема после очистки пуста.
        Возвращает новый путь.
        """
        topic = clean_topic(topic)
        with self._lock:
            run = self._run
            # Заметка последней Сессии остаётся в своей папке, даже если папку вида сменили, пока Сессия шла.
            shown = run is not None and note_path.resolve() == run.note.path.resolve()
            folders = [(run.kind, run.note.path.parent)] if shown and run is not None else []
            try:
                note_path, kind = find_session_note(note_path, [*folders, *self._load_settings().kind_folders()])
            except NoteNotFound as error:
                raise CommandRejected(str(error)) from error
            if shown and self._state is not SessionState.NONE:
                raise CommandRejected(f"Сессия с этой Заметкой ещё {self._state.label}")
            note = run.note if shown and run is not None else Note(note_path)
            if note.has_topic:
                raise CommandRejected(f"У Заметки уже есть Тема: {note_path.name}")
            if not topic:
                raise CommandRejected("Тема пуста")
            try:
                new_path = note.rename_with_topic(topic)
            except OSError as error:
                raise CommandRejected(f"Заметку не удалось переименовать: {error.strerror or error}") from error
            self._agent.note_renamed(note_path, new_path, kind)
            if shown and run is not None:
                run.topic = run.named_topic = topic
                self._publish(StateChanged(self.view()))
            return new_path

    def restore(self) -> None:
        """При запуске службы: Сессия, которую застал перерыв в работе службы, по сведениям на диске.

        Перерыв — от последней активности до сейчас. Короче времени автостопа — Сессия продолжается
        в ту же Заметку: идущая — с отметкой о пропуске и заново запущенным захватом, на Паузе —
        на Паузе. Дольше (или Сессия уже завершалась) — завершается временем последней активности.
        Об итоге — системное уведомление; если Заметки больше нет, сведения удаляются молча.
        """
        record = self._store.load()
        if record is None:
            return
        if not record.note.is_file():
            log.warning("Заметки %s больше нет — сведения о Сессии удалены", record.note)
            self._store.remove()
            return
        settings = self._load_settings()
        now = self._clock.now()
        note = Note(record.note)
        run = _Run(
            kind=record.kind,
            started_at=record.started_at,
            note=note,
            sources={
                source: _SourceRun(UtteranceCutter(source, settings.recognition, self._speech_detector()))
                for source in SOURCES_BY_KIND[record.kind]
            },
            auto_stop=timedelta(minutes=settings.auto_stop_minutes),
            last_speech_at=record.last_speech_at or record.last_active_at,
            topic=record.topic,
            named_topic=record.named_topic,
            link=record.link,
            pinned={source: settings.sources.for_source(source) for source in SOURCES_BY_KIND[record.kind]},
            capture_base=record.captured_seconds,
        )
        run.captured_before = record.captured_seconds
        run.alive_at = record.last_active_at
        run.paused_at = record.paused_at
        run.transcript.restore(note.transcript_markdown())
        with self._lock:
            self._run = run
            threading.Thread(
                target=self._recognize_until_finished, args=(run,), name="spook-recognition", daemon=True
            ).start()
            since = record.last_active_at
            if record.state is SessionState.FINISHING:
                log.info("Сессия %s завершалась до перерыва — завершается", note.path.name)
                self._begin_finishing(run, ended_at=since)
                run.work.put(_FINISH)
                return
            self._state = record.state
            notice = self._handle_break(run, since, now)
        if notice.finished:
            run.work.put(_FINISH)
        elif self._state is SessionState.RUNNING:
            try:
                self._start_captures(run)
            except CaptureUnavailable as error:
                log.warning("После перерыва захват не начат: %s", error)
                self._stop_captures(run)
                with self._lock:  # захвата нет — Сессия ждёт на Паузе, пока его не будет
                    run.close_interval(now)
                    run.paused_at = now
                    self._cancel_auto_stop(run)
                    self._state = SessionState.PAUSED
                    self._save(run)
                    self._publish(StateChanged(self.view()))
                notice = _Notice("Сессия на Паузе", f"Захват после перерыва не начат: {error}")
        send_notification(settings.programs.notify, notice.title, notice.body)

    # --- состояние для страниц --------------------------------------------------

    def view(self) -> SessionView:
        with self._lock:
            run = self._run
            agent = self._agent.jobs()
            if run is None:
                return SessionView(state=self._state, agent=agent)
            return SessionView(
                state=self._state,
                kind=run.kind,
                started_at=run.started_at,
                elapsed_seconds=max(0, int(run.captured_seconds(self._clock.now()))),
                note_path=run.note.path,
                topic=run.topic,
                link=run.link,
                entries=run.transcript.entries,
                sources=tuple(SourceView(source, name) for source, name in run.devices.items()),
                warnings=tuple(run.warnings.values()),
                backlog_seconds=run.backlog_samples / SAMPLE_RATE,
                backlog_segments=run.backlog_segments,
                agent=agent,
            )

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[SessionView, Subscription]:
        """Подписка на изменения вместе с полным текущим состоянием — без щели между ними."""
        with self._lock:
            subscription = Subscription(loop=loop, queue=asyncio.Queue())
            self._subscribers.append(subscription)
            return self.view(), subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    # --- внутреннее -------------------------------------------------------------

    def _begin_finishing(self, run: _Run, ended_at: datetime | None = None) -> None:
        """Переводит Сессию в «завершается» — под замком; общий путь «Стоп» и автостопа.
        `ended_at` — время конца, если Сессия кончилась раньше, чем это стало известно (перерыв)."""
        self._cancel_auto_stop(run)
        self._cancel_activity(run)
        run.ended_at = ended_at
        run.alive_at = ended_at or self._clock.now()  # если служба оборвётся теперь, конец — этот момент
        run.close_interval(run.alive_at)
        self._state = SessionState.FINISHING  # звук, пришедший с этого момента, отбрасывается
        self._save(run)
        self._publish(StateChanged(self.view()))

    def _finish_capture(self, run: _Run) -> None:
        """Останавливает захват и отдаёт очереди остаток звука и команду конца — вне замка, после
        `_begin_finishing`."""
        self._stop_captures(run)
        with self._lock:
            self._flush_cutters(run)
            run.work.put(_FINISH)

    # --- автостоп ----------------------------------------------------------------

    def _schedule_auto_stop(self, run: _Run) -> None:
        """Проверка на момент, когда тишина достигнет времени автостопа, если речи больше не будет."""
        run.auto_stop_timer = self._clock.call_at(
            run.last_speech_at + run.auto_stop, lambda: self._check_auto_stop(run)
        )

    @staticmethod
    def _cancel_auto_stop(run: _Run) -> None:
        if run.auto_stop_timer is not None:
            run.auto_stop_timer.cancel()
            run.auto_stop_timer = None

    def _check_auto_stop(self, run: _Run) -> None:
        with self._lock:
            if self._run is not run or self._state is not SessionState.RUNNING:
                return  # Сессия уже на Паузе или завершается: таймер опоздал
            # Проверка, опоздавшая из-за сна, решает по правилам перерыва: конец — последняя активность.
            notice = self._notice_break(run) or self._auto_stop_notice(run)
            if notice is None:
                self._schedule_auto_stop(run)  # с тех пор была речь: ждать от неё
                return
        self._finish_and_notify(run, notice)

    def _auto_stop_notice(self, run: _Run) -> _Notice | None:
        """Тишина достигла времени автостопа — Сессия переходит в «завершается»; под замком."""
        if self._clock.now() - run.last_speech_at < run.auto_stop:
            return None
        silence = _silence_label(run.auto_stop)
        log.info("Автостоп: %s без речи, Сессия завершается", silence)
        self._begin_finishing(run)
        return _Notice("Сессия завершена", f"{silence} без речи", finished=True)

    # --- сведения на диске и перерыв в работе службы ------------------------------

    def _save(self, run: _Run) -> None:
        """Сведения об идущей Сессии — на диск; вызывается под замком при каждой смене."""
        self._store.save(
            SessionRecord(
                note=run.note.path,
                kind=run.kind,
                topic=run.topic,
                named_topic=run.named_topic,
                link=run.link,
                state=self._state,
                started_at=run.started_at,
                captured_seconds=run.captured_seconds(run.alive_at),
                last_active_at=run.alive_at,
                paused_at=run.paused_at,
                last_speech_at=run.last_speech_at,
            )
        )

    def _schedule_activity(self, run: _Run) -> None:
        run.activity_timer = self._clock.call_at(
            run.alive_at + timedelta(seconds=ACTIVITY_SECONDS), lambda: self._mark_activity(run)
        )

    @staticmethod
    def _cancel_activity(run: _Run) -> None:
        if run.activity_timer is not None:
            run.activity_timer.cancel()
            run.activity_timer = None

    def _handle_break(self, run: _Run, since: datetime, until: datetime) -> _Notice:
        """Перерыв в работе службы с `since` (последняя активность) до `until` — под замком.

        Перерыв — тишина: у идущей Сессии с временем автостопа сравнивается вся тишина вместе
        с перерывом (от последней Реплики), у Сессии на Паузе — сам перерыв. Короче: идущая Сессия
        продолжается с отметкой о пропуске, отсчёт тишины — от последней Реплики; на Паузе — остаётся
        на Паузе. Дольше — Сессия переходит в «завершается» с концом в `since`; захват и остаток звука
        вызывающий отдаёт `_finish_capture` вне замка. Возвращает уведомление.
        """
        period = f"{since:%H:%M}–{until:%H:%M}"
        quiet_since = min(since, run.last_speech_at) if self._state is SessionState.RUNNING else since
        if until - quiet_since >= run.auto_stop:
            log.info("Перерыв в работе службы %s: без речи дольше автостопа — Сессия завершается", period)
            self._begin_finishing(run, ended_at=since)
            return _Notice(
                "Сессия завершена",
                f"Перерыв в работе Spook {period} — без речи дольше {_silence_label(run.auto_stop)}",
                True,
            )
        log.info("Перерыв в работе службы %s — Сессия продолжается", period)
        self._cancel_activity(run)
        if self._state is SessionState.RUNNING:
            run.close_interval(since)
            self._flush_cutters(run)  # звук до перерыва — в распознавание, перед отметкой
            mark = _gap_mark(run, "Пропуск", since, until)
            run.open_interval(until)
            self._cancel_auto_stop(run)  # отсчёт тишины продолжается от последней Реплики
            self._schedule_auto_stop(run)
            if run.backlog_segments:  # отметка — через очередь, после звука до перерыва
                run.work.put(mark)
            else:
                self._record_mark(run, mark)
            notice = _Notice("Сессия продолжена", f"Перерыв в работе Spook {period}, в Заметке отметка о пропуске")
        else:
            notice = _Notice("Сессия на Паузе", f"Перерыв в работе Spook {period}, Сессия осталась на Паузе")
        run.alive_at = until
        self._schedule_activity(run)
        self._save(run)
        self._publish(StateChanged(self.view()))
        return notice

    def _notice_break(self, run: _Run) -> _Notice | None:
        """Скачок часов без перезапуска (сон ноутбука) — под замком: если с последней активности
        прошло больше `BREAK_SECONDS`, это перерыв, и он разбирается по общим правилам."""
        now = self._clock.now()
        if now - run.alive_at <= timedelta(seconds=BREAK_SECONDS):
            return None
        return self._handle_break(run, run.alive_at, now)

    def _finish_and_notify(self, run: _Run, notice: _Notice) -> None:
        """Вне замка: остаток захвата завершающейся Сессии — в очередь, уведомление — пользователю."""
        if notice.finished:
            self._finish_capture(run)
        send_notification(self._load_settings().programs.notify, notice.title, notice.body)

    def _mark_activity(self, run: _Run) -> None:
        """Служба жива: время последней активности обновляется и пишется на диск."""
        with self._lock:
            if self._run is not run or self._state not in (SessionState.RUNNING, SessionState.PAUSED):
                return
            notice = self._notice_break(run)
            if notice is None:
                run.alive_at = self._clock.now()
                self._save(run)
                self._schedule_activity(run)
                return
        self._finish_and_notify(run, notice)

    # --- захват и распознавание ----------------------------------------------------

    def _start_captures(self, run: _Run) -> None:
        """Начинает захват всех Источников вида — с закреплённых в настройках устройств или с устройств
        по умолчанию; если хоть один недоступен — `CaptureUnavailable`."""
        for source, state in run.sources.items():
            if state.capture is None:
                state.capture = self._audio_input.start(
                    source, partial(self._on_audio, source), self._on_device_change, pinned=run.pinned.get(source, "")
                )
                run.devices[source] = state.capture.device_name

    @staticmethod
    def _stop_captures(run: _Run) -> None:
        # Захват останавливается вне замка: его поток может как раз ждать `_on_audio`.
        for state in run.sources.values():
            if state.capture is not None:
                state.capture.stop()
                state.capture = None

    def _flush_cutters(self, run: _Run) -> None:
        """Незакрытые Реплики всех Источников закрываются и уходят в распознавание; придержанные
        Реплики, которых больше нечему опередить, записываются."""
        for state in run.sources.values():
            remainder = state.cutter.flush()
            if remainder is not None:
                self._enqueue_segment(run, remainder)
        self._write_ready(run)

    def _on_audio(self, source: Source, samples: np.ndarray) -> None:
        level = Level.of(samples)
        with self._lock:
            run = self._run
            if run is None or self._state is not SessionState.RUNNING:
                return
            notice = self._notice_break(run)  # звук после сна может прийти раньше таймеров
            if notice is not None:
                # Захват останавливают не из его же потока.
                threading.Thread(
                    target=self._finish_and_notify, args=(run, notice), name="spook-break", daemon=True
                ).start()
                if notice.finished:
                    return
            for segment in run.sources[source].cutter.push(samples):
                self._enqueue_segment(run, segment)
            self._publish(LevelsChanged({source: level}))

    def _enqueue_segment(self, run: _Run, segment: AudioSegment) -> None:
        """Отрезок встаёт в очередь распознавания — одну на оба Источника — и считается
        в отставании; вызывается под замком."""
        run.sources[segment.source].queued.append(segment.start)
        run.work.put(segment)
        run.backlog_samples += len(segment.samples)
        run.backlog_segments += 1
        self._publish(BacklogChanged(self.view()))

    def _on_device_change(self, change: DeviceChange) -> None:
        """Захват перешёл на другое устройство: название на странице обновляется, в Транскрипт идёт
        отметка, при пропаже устройства — ещё и предупреждение на страницу до конца Сессии."""
        with self._lock:
            run = self._run
            if run is None or self._state is not SessionState.RUNNING:
                return  # событие захвата, который уже остановлен «Паузой» или «Стоп»
            now = self._clock.now()
            previous = run.devices.get(change.source, "")
            run.devices[change.source] = change.device_name or NO_DEVICE
            mark = DeviceMark(_mark_time(run, now), change.source, change.device_name, lost=previous if change.lost else "")
            if change.lost:
                run.warnings[change.source] = mark.text
                # Закреплённое устройство пропало: до конца Сессии захват следует за устройством по умолчанию,
                # иначе «Продолжить» после Паузы упёрлось бы в отсутствующее устройство.
                run.pinned.pop(change.source, None)
            # Звук с прежнего устройства закрывается как Реплика перед отметкой; отметка идёт через очередь
            # распознавания, чтобы встать в Транскрипте на своё место.
            remainder = run.sources[change.source].cutter.flush()
            if remainder is not None:
                self._enqueue_segment(run, remainder)
            run.work.put(mark)
            self._publish(StateChanged(self.view()))

    def _recognize_until_finished(self, run: _Run) -> None:
        while True:
            item = run.work.get()
            if item is _FINISH:
                break
            if isinstance(item, Mark):
                self._record_mark(run, item)
            else:
                try:
                    self._recognize(run, item)
                finally:
                    self._settle_backlog(run, item)
        self._finish(run)

    def _settle_backlog(self, run: _Run, segment: AudioSegment) -> None:
        """Отрезок распознан (или не удался) — он больше не ждёт."""
        with self._lock:
            run.backlog_samples -= len(segment.samples)
            run.backlog_segments -= 1
            self._publish(BacklogChanged(self.view()))

    def _record_mark(self, run: _Run, mark: Mark) -> None:
        with self._lock:
            # Всё, что ещё придержано, сказано до Паузы или смены устройства: отметка идёт после.
            self._write_held(run)
            run.transcript.add_mark(mark)
            run.note.append_mark(mark)
            self._publish(MarkAdded(mark))

    def _recognize(self, run: _Run, segment: AudioSegment) -> None:
        try:
            text = self._recognizer.recognize(segment).strip()
        except Exception:
            log.exception("Распознавание отрезка %s не удалось", format_offset(segment.start))
            text = ""
        with self._lock:
            run.sources[segment.source].queued.popleft()
            if text:
                run.last_speech_at = self._clock.now()  # речь любого Источника сбрасывает отсчёт автостопа
                if self._run is run and self._state is not SessionState.NONE:
                    self._save(run)  # перерыв после неё считается тишиной от этого момента
                utterance = Utterance(segment.source, segment.start, segment.start + segment.duration, text)
                insort(run.held, utterance, key=lambda held: held.start)
            self._write_ready(run)

    def _write_ready(self, run: _Run) -> None:
        """Реплики идут по времени начала: распознанная Реплика записывается, только когда ни один
        другой Источник уже не может дать Реплику, начавшуюся раньше; до тех пор она придерживается."""
        while run.held:
            utterance = run.held[0]
            others = (state for source, state in run.sources.items() if source is not utterance.source)
            if any(state.frontier() < utterance.start for state in others):
                return
            self._write(run, run.held.pop(0))

    def _write_held(self, run: _Run) -> None:
        while run.held:
            self._write(run, run.held.pop(0))

    def _write(self, run: _Run, utterance: Utterance) -> None:
        paragraph, started = run.transcript.add(utterance)
        if started:
            run.note.append_paragraph(paragraph)
            self._publish(ParagraphAdded(paragraph))
        else:
            run.note.extend_paragraph(utterance.text)
            self._publish(ParagraphUpdated(paragraph))

    def _finish(self, run: _Run) -> None:
        with self._lock:
            run.warnings.clear()  # предупреждения о пропавших устройствах живут до конца Сессии
            self._write_held(run)
            has_utterances = bool(run.transcript.paragraphs)
            if has_utterances:
                run.note.set_end(run.ended_at or self._clock.now())
                if run.topic and run.topic != run.named_topic:
                    try:
                        run.note.rename_with_topic(run.topic)
                    except OSError:
                        log.exception("Заметку %s не удалось переименовать по Теме", run.note.path.name)
                    else:
                        run.named_topic = run.topic
            else:
                run.note.path.unlink(missing_ok=True)
                self._run = None
            self._store.remove()
            self._state = SessionState.NONE
            self._publish(StateChanged(self.view()))
            if has_utterances:  # Заметка уходит Агенту уже под окончательным именем
                # Скилл, модель и effort — из настроек на этот запуск Агента; рабочий каталог — папка
                # Заметки: папку вида могли сменить в настройках, пока Сессия шла.
                kind_settings = replace(self._load_settings().for_kind(run.kind), folder=run.note.path.parent)
                self._agent.enqueue(AgentJob.for_note(run.note.path, run.kind, kind_settings))

    def _on_agent_change(self) -> None:
        with self._lock:
            self._publish(AgentChanged(self._agent.jobs()))

    def _publish(self, event: Event) -> None:
        for subscription in list(self._subscribers):
            try:
                subscription.loop.call_soon_threadsafe(subscription.queue.put_nowait, event)
            except RuntimeError:  # цикл событий страницы уже закрыт
                self._subscribers.remove(subscription)


@dataclass(frozen=True)
class _Notice:
    """Итог перерыва в работе службы или автостопа: уведомление и признак «Сессия завершается»."""

    title: str
    body: str
    finished: bool = False


def _pause_mark(run: _Run, resumed_at: datetime) -> PauseMark:
    assert run.paused_at is not None
    return _gap_mark(run, "Пауза", run.paused_at, resumed_at)


def _gap_mark(run: _Run, title: str, since: datetime, until: datetime) -> PauseMark:
    """Отметка о Паузе или пропуске по виду Сессии: Лекция — время от начала Сессии без учёта
    предыдущих Пауз и пропусков (начало — захваченное время на момент начала, конец — оно же плюс
    длительность), Встреча — время по часам. Интервал «идёт» к этому моменту уже закрыт."""
    if run.kind is SessionKind.LECTURE:
        start = run.captured_before
        end = start + (until - since).total_seconds()
        return PauseMark(format_offset(start), format_offset(end), title)
    return PauseMark(f"{since:%H:%M}", f"{until:%H:%M}", title)


def _mark_time(run: _Run, now: datetime) -> str:
    """Время отметки по виду Сессии: Лекция — захваченное время от начала Сессии, Встреча — по часам."""
    if run.kind is SessionKind.LECTURE:
        return format_offset(run.captured_seconds(now))
    return f"{now:%H:%M}"


def _folder_name(kind: SessionKind) -> str:
    return {SessionKind.LECTURE: "Лекций", SessionKind.MEETING: "Встреч"}[kind]


def _silence_label(auto_stop: timedelta) -> str:
    """Время автостопа словами: целые минуты — «5 минут», иначе секунды — «90 секунд»."""
    seconds = round(auto_stop.total_seconds())
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60} {_plural(seconds // 60, 'минута', 'минуты', 'минут')}"
    return f"{seconds} {_plural(seconds, 'секунда', 'секунды', 'секунд')}"


def _plural(number: int, one: str, few: str, many: str) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many
