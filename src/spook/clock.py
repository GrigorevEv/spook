from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable, Protocol


class Timer(Protocol):
    def cancel(self) -> None:
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        ...

    def call_at(self, moment: datetime, callback: Callable[[], None]) -> Timer:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()

    def call_at(self, moment: datetime, callback: Callable[[], None]) -> Timer:
        delay = max(0.0, (moment - self.now()).total_seconds())
        timer = threading.Timer(delay, callback)
        timer.name = "spook-timer"
        timer.daemon = True
        timer.start()
        return timer


class _ManualTimer:
    def __init__(self, moment: datetime, callback: Callable[[], None]) -> None:
        self.moment = moment
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._lock = threading.Lock()
        self._timers: list[_ManualTimer] = []

    def now(self) -> datetime:
        return self._now

    def call_at(self, moment: datetime, callback: Callable[[], None]) -> Timer:
        timer = _ManualTimer(moment, callback)
        with self._lock:
            self._timers.append(timer)
        return timer

    def advance(self, seconds: float) -> None:
        target = self._now + timedelta(seconds=seconds)
        while (timer := self._pop_due(target)) is not None:
            self._now = max(self._now, timer.moment)
            if not timer.cancelled:
                timer.callback()
        self._now = target

    def sleep(self, seconds: float) -> None:
        shift = timedelta(seconds=seconds)
        with self._lock:
            for timer in self._timers:
                timer.moment += shift
            self._now += shift

    def set(self, moment: datetime) -> None:
        self._now = moment
        while (timer := self._pop_due(moment)) is not None:
            if not timer.cancelled:
                timer.callback()

    def _pop_due(self, until: datetime) -> _ManualTimer | None:
        with self._lock:
            self._timers = [timer for timer in self._timers if not timer.cancelled]
            due = [timer for timer in self._timers if timer.moment <= until]
            if not due:
                return None
            earliest = min(due, key=lambda timer: timer.moment)
            self._timers.remove(earliest)
            return earliest
