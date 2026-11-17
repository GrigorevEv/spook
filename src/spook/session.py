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
from spook.recognizer import RecognitionUnavailable, Recognizer
from spook.session_record import SessionRecord, SessionState, SessionStore
from spook.settings import SessionKind, Settings
from spook.transcript import DeviceMark, Entry, Mark, Paragraph, PauseMark, Transcript, Utterance, format_offset

log = logging.getLogger(__name__)

NO_DEVICE = "нет устройства"


class CommandRejected(Exception):
    pass


@dataclass(frozen=True)
class SourceView:
    source: Source
    device_name: str


@dataclass(frozen=True)
class SessionView:
    state: SessionState
    kind: SessionKind | None = None
    started_at: datetime | None = None
    elapsed_seconds: int = 0
    note_path: Path | None = None
    topic: str = ""
    link: str = ""
    entries: tuple[Entry, ...] = ()
    sources: tuple[SourceView, ...] = ()
    warnings: tuple[str, ...] = ()
    backlog_seconds: float = 0.0
    backlog_segments: int = 0
    agent: tuple[AgentJobView, ...] = ()

    @property
    def elapsed_label(self) -> str:
        return format_offset(self.elapsed_seconds)

    @property
    def backlog_label(self) -> str:
        return f"{math.ceil(self.backlog_seconds)} с"

    @property
    def backlog_visible(self) -> bool:
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
    paragraph: Paragraph


@dataclass(frozen=True)
class MarkAdded:
    mark: Mark


@dataclass(frozen=True)
class LevelsChanged:
    levels: dict[Source, Level]


@dataclass(frozen=True)
class AgentChanged:
    jobs: tuple[AgentJobView, ...]


@dataclass(frozen=True)
class BacklogChanged:
    view: SessionView


Event = StateChanged | ParagraphAdded | ParagraphUpdated | MarkAdded | LevelsChanged | AgentChanged | BacklogChanged


@dataclass
class Subscription:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[Event]


_FINISH = object()

ACTIVITY_SECONDS = 30
BREAK_SECONDS = 90

SOURCES_BY_KIND = {
    SessionKind.MEETING: (Source.SYSTEM, Source.MIC),
    SessionKind.LECTURE: (Source.SYSTEM,),
}


@dataclass
class _SourceRun:
    cutter: UtteranceCutter
    capture: Capture | None = None
    queued: deque[float] = field(default_factory=deque)

    def earliest_pending_start(self) -> float:
        if self.queued:
            return self.queued[0]
        if self.capture is not None or self.cutter.buffered_seconds > 0:
            return self.cutter.next_segment_start
        return math.inf


@dataclass
class _Run:
    kind: SessionKind
    started_at: datetime
    note: Note
    sources: dict[Source, _SourceRun]
    auto_stop: timedelta
    last_speech_at: datetime
    auto_stop_timer: Timer | None = None
    alive_at: datetime = field(init=False)
    activity_timer: Timer | None = None
    topic: str = ""
    named_topic: str = ""
    link: str = ""
    transcript: Transcript = field(init=False)
    work: queue.Queue = field(default_factory=queue.Queue)
    held: list[Utterance] = field(default_factory=list)
    devices: dict[Source, str] = field(default_factory=dict)
    pinned: dict[Source, str] = field(default_factory=dict)
    warnings: dict[Source, str] = field(default_factory=dict)
    running_since: datetime | None = None
    captured_before: float = 0.0
    intervals: list[tuple[float, datetime]] = field(default_factory=list)
    paused_at: datetime | None = None
    backlog_samples: int = 0
    backlog_segments: int = 0
    ended_at: datetime | None = None
    captured_before_restart: float = 0.0

    def __post_init__(self) -> None:
        self.alive_at = self.started_at
        if self.kind is SessionKind.MEETING:
            self.transcript = Transcript(time_label=self.clock_label, speakers=True)
        else:
            self.transcript = Transcript(time_label=self.offset_label)

    def open_interval(self, now: datetime) -> None:
        self.running_since = now
        self.intervals.append((self.captured_before, now))

    def queue_mark_after_segments(self, mark: Mark) -> None:
        self.work.put(mark)

    def close_interval(self, now: datetime) -> None:
        self.captured_before = self.captured_seconds(now)
        self.running_since = None

    def captured_seconds(self, now: datetime) -> float:
        if self.running_since is None:
            return self.captured_before
        return self.captured_before + (now - self.running_since).total_seconds()

    def clock_at(self, offset: float) -> datetime:
        for position, moment in reversed(self.intervals):
            if offset >= position:
                return moment + timedelta(seconds=offset - position)
        return self.started_at + timedelta(seconds=offset)

    def clock_label(self, offset: float) -> str:
        return f"{self.clock_at(self.captured_before_restart + offset):%H:%M}"

    def offset_label(self, offset: float) -> str:
        return format_offset(self.captured_before_restart + offset)


class SessionService:
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

    def start(self, kind: SessionKind, topic: str = "", link: str = "") -> SessionView:
        with self._lock:
            if self._state is not SessionState.NONE:
                raise CommandRejected(f"Сессия уже {self._state.label}")
            try:
                self._recognizer.check_available()
            except RecognitionUnavailable as error:
                raise CommandRejected(str(error)) from error
            settings = self._load_settings()
            folder = settings.for_kind(kind).folder
            started_at = self._clock.now()
            topic = clean_topic(topic)
            link = clean_link(link) if kind is SessionKind.LECTURE else ""
            try:
                sources = {
                    source: _SourceRun(UtteranceCutter(source, settings.recognition, self._speech_detector()))
                    for source in SOURCES_BY_KIND[kind]
                }
            except RecognitionUnavailable as error:
                raise CommandRejected(str(error)) from error
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
                note.path.unlink(missing_ok=True)
                unavailable = error
            else:
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
        self._stop_captures_outside_lock(run)
        raise CommandRejected(str(unavailable)) from unavailable

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
            self._cancel_auto_stop(run)
            self._state = SessionState.PAUSED
            self._save(run)
            self._publish(StateChanged(self.view()))
        self._stop_captures_outside_lock(run)
        with self._lock:
            self._flush_cutters(run)
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
                unavailable = error
            else:
                now = self._clock.now()
                mark = _pause_mark(run, resumed_at=now)
                run.paused_at = None
                run.open_interval(now)
                run.last_speech_at = now
                self._state = SessionState.RUNNING
                run.queue_mark_after_segments(mark)
                self._schedule_auto_stop(run)
                self._save(run)
                self._publish(StateChanged(self.view()))
                return self.view()
        self._stop_captures_outside_lock(run)
        raise CommandRejected(str(unavailable)) from unavailable

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
        with self._lock:
            run = self._changeable_run()
            run.topic = clean_topic(topic)
            self._save(run)
            self._publish(StateChanged(self.view()))
            return self.view()

    def set_link(self, link: str) -> SessionView:
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
        if self._state is SessionState.NONE:
            raise CommandRejected("Сессии нет")
        if self._state is SessionState.FINISHING:
            raise CommandRejected("Сессия уже завершается")
        assert self._run is not None
        return self._run

    def rename_note(self, note_path: Path, topic: str) -> Path:
        topic = clean_topic(topic)
        with self._lock:
            run = self._run
            is_last_session_note = run is not None and note_path.resolve() == run.note.path.resolve()
            folders = [(run.kind, run.note.path.parent)] if is_last_session_note and run is not None else []
            try:
                note_path, kind = find_session_note(note_path, [*folders, *self._load_settings().kind_folders()])
            except NoteNotFound as error:
                raise CommandRejected(str(error)) from error
            if is_last_session_note and self._state is not SessionState.NONE:
                raise CommandRejected(f"Сессия с этой Заметкой ещё {self._state.label}")
            note = run.note if is_last_session_note and run is not None else Note(note_path)
            if note.has_topic:
                raise CommandRejected(f"У Заметки уже есть Тема: {note_path.name}")
            if not topic:
                raise CommandRejected("Тема пуста")
            try:
                new_path = note.rename_with_topic(topic)
            except OSError as error:
                raise CommandRejected(f"Заметку не удалось переименовать: {error.strerror or error}") from error
            self._agent.note_renamed(note_path, new_path, kind)
            if is_last_session_note and run is not None:
                run.topic = run.named_topic = topic
                self._publish(StateChanged(self.view()))
            return new_path

    def restore(self) -> None:
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
            captured_before_restart=record.captured_seconds,
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
                self._stop_captures_outside_lock(run)
                with self._lock:
                    run.close_interval(now)
                    run.paused_at = now
                    self._cancel_auto_stop(run)
                    self._state = SessionState.PAUSED
                    self._save(run)
                    self._publish(StateChanged(self.view()))
                notice = _Notice("Сессия на Паузе", f"Захват после перерыва не начат: {error}")
        send_notification(settings.programs.notify, notice.title, notice.body)

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
        with self._lock:
            subscription = Subscription(loop=loop, queue=asyncio.Queue())
            self._subscribers.append(subscription)
            return self.view(), subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def _begin_finishing(self, run: _Run, ended_at: datetime | None = None) -> None:
        self._cancel_auto_stop(run)
        self._cancel_activity(run)
        run.ended_at = ended_at or self._clock.now()
        run.alive_at = run.ended_at
        run.close_interval(run.alive_at)
        self._state = SessionState.FINISHING
        self._save(run)
        self._publish(StateChanged(self.view()))

    def _finish_capture(self, run: _Run) -> None:
        self._stop_captures_outside_lock(run)
        with self._lock:
            self._flush_cutters(run)
            run.work.put(_FINISH)

    def _schedule_auto_stop(self, run: _Run) -> None:
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
                return
            notice = self._notice_clock_jump(run) or self._auto_stop_notice(run)
            if notice is None:
                self._schedule_auto_stop(run)
                return
        self._finish_and_notify(run, notice)

    def _auto_stop_notice(self, run: _Run) -> _Notice | None:
        if self._clock.now() - run.last_speech_at < run.auto_stop:
            return None
        silence = _silence_label(run.auto_stop)
        log.info("Автостоп: %s без речи, Сессия завершается", silence)
        self._begin_finishing(run)
        return _Notice("Сессия завершена", f"{silence} без речи", finished=True)

    def _save(self, run: _Run) -> None:
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
            self._flush_cutters(run)
            mark = _gap_mark(run, "Пропуск", since, until)
            run.open_interval(until)
            self._cancel_auto_stop(run)
            self._schedule_auto_stop(run)
            if run.backlog_segments:
                run.queue_mark_after_segments(mark)
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

    def _notice_clock_jump(self, run: _Run) -> _Notice | None:
        now = self._clock.now()
        if now - run.alive_at <= timedelta(seconds=BREAK_SECONDS):
            return None
        return self._handle_break(run, run.alive_at, now)

    def _finish_and_notify(self, run: _Run, notice: _Notice) -> None:
        if notice.finished:
            self._finish_capture(run)
        send_notification(self._load_settings().programs.notify, notice.title, notice.body)

    def _finish_and_notify_off_capture_thread(self, run: _Run, notice: _Notice) -> None:
        threading.Thread(
            target=self._finish_and_notify, args=(run, notice), name="spook-break", daemon=True
        ).start()

    def _mark_activity(self, run: _Run) -> None:
        with self._lock:
            if self._run is not run or self._state not in (SessionState.RUNNING, SessionState.PAUSED):
                return
            notice = self._notice_clock_jump(run)
            if notice is None:
                run.alive_at = self._clock.now()
                self._save(run)
                self._schedule_activity(run)
                return
        self._finish_and_notify(run, notice)

    def _start_captures(self, run: _Run) -> None:
        for source, state in run.sources.items():
            if state.capture is None:
                state.capture = self._audio_input.start(
                    source, partial(self._on_audio, source), self._on_device_change, pinned=run.pinned.get(source, "")
                )
                run.devices[source] = state.capture.device_name

    @staticmethod
    def _stop_captures_outside_lock(run: _Run) -> None:
        for state in run.sources.values():
            if state.capture is not None:
                state.capture.stop()
                state.capture = None

    def _flush_cutters(self, run: _Run) -> None:
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
            notice = self._notice_clock_jump(run)
            if notice is not None:
                self._finish_and_notify_off_capture_thread(run, notice)
                if notice.finished:
                    return
            for segment in run.sources[source].cutter.push(samples):
                self._enqueue_segment(run, segment)
            self._publish(LevelsChanged({source: level}))

    def _enqueue_segment(self, run: _Run, segment: AudioSegment) -> None:
        run.sources[segment.source].queued.append(segment.start)
        run.work.put(segment)
        run.backlog_samples += len(segment.samples)
        run.backlog_segments += 1
        self._publish(BacklogChanged(self.view()))

    def _on_device_change(self, change: DeviceChange) -> None:
        with self._lock:
            run = self._run
            if run is None or self._state is not SessionState.RUNNING:
                return
            now = self._clock.now()
            previous = run.devices.get(change.source, "")
            run.devices[change.source] = change.device_name or NO_DEVICE
            mark = DeviceMark(_mark_time(run, now), change.source, change.device_name, lost=previous if change.lost else "")
            if change.lost:
                run.warnings[change.source] = mark.text
                run.pinned.pop(change.source, None)
            remainder = run.sources[change.source].cutter.flush()
            if remainder is not None:
                self._enqueue_segment(run, remainder)
            run.queue_mark_after_segments(mark)
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
        with self._lock:
            run.backlog_samples -= len(segment.samples)
            run.backlog_segments -= 1
            self._publish(BacklogChanged(self.view()))

    def _record_mark(self, run: _Run, mark: Mark) -> None:
        with self._lock:
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
                run.last_speech_at = self._clock.now()
                if self._run is run and self._state is not SessionState.NONE:
                    self._save(run)
                utterance = Utterance(segment.source, segment.start, segment.start + segment.duration, text)
                insort(run.held, utterance, key=lambda held: held.start)
            self._write_ready(run)

    def _write_ready(self, run: _Run) -> None:
        while run.held:
            utterance = run.held[0]
            others = (state for source, state in run.sources.items() if source is not utterance.source)
            if any(state.earliest_pending_start() < utterance.start for state in others):
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
            run.warnings.clear()
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
            if has_utterances:
                kind_settings = replace(self._load_settings().for_kind(run.kind), folder=run.note.path.parent)
                self._agent.enqueue(AgentJob.for_note(run.note.path, run.kind, kind_settings))

    def _on_agent_change(self) -> None:
        with self._lock:
            self._publish(AgentChanged(self._agent.jobs()))

    def _publish(self, event: Event) -> None:
        for subscription in list(self._subscribers):
            try:
                subscription.loop.call_soon_threadsafe(subscription.queue.put_nowait, event)
            except RuntimeError:
                self._subscribers.remove(subscription)


@dataclass(frozen=True)
class _Notice:
    title: str
    body: str
    finished: bool = False


def _pause_mark(run: _Run, resumed_at: datetime) -> PauseMark:
    assert run.paused_at is not None
    return _gap_mark(run, "Пауза", run.paused_at, resumed_at)


def _gap_mark(run: _Run, title: str, since: datetime, until: datetime) -> PauseMark:
    if run.kind is SessionKind.LECTURE:
        start = run.captured_before
        end = start + (until - since).total_seconds()
        return PauseMark(format_offset(start), format_offset(end), title)
    return PauseMark(f"{since:%H:%M}", f"{until:%H:%M}", title)


def _mark_time(run: _Run, now: datetime) -> str:
    if run.kind is SessionKind.LECTURE:
        return format_offset(run.captured_seconds(now))
    return f"{now:%H:%M}"


def _folder_name(kind: SessionKind) -> str:
    return {SessionKind.LECTURE: "Лекций", SessionKind.MEETING: "Встреч"}[kind]


def _silence_label(auto_stop: timedelta) -> str:
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
