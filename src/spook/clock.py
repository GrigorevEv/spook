"""Часы службы: настоящие и управляемые (для тестов автостопа и перерывов без ожидания).

Кроме текущего времени часы дают таймер: «вызови в такой-то момент». У настоящих часов он идёт
по системному времени, у управляемых срабатывает, когда тест сдвигает время до этого момента, —
так проверка автостопа не зависит ни от входящего звука, ни от настоящего ожидания.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable, Protocol


class Timer(Protocol):
    def cancel(self) -> None:
        """Отменяет вызов; после срабатывания ничего не делает."""
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущее местное время с часовым поясом."""
        ...

    def call_at(self, moment: datetime, callback: Callable[[], None]) -> Timer:
        """Вызывает `callback` один раз, когда часы дойдут до `moment` (сразу, если момент уже прошёл);
        из какого потока — зависит от часов, поэтому `callback` должен быть потокобезопасным."""
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
    """Часы, которые идут только по команде; таймеры срабатывают при сдвиге времени, в потоке
    того, кто его сдвинул.

    `advance` — время течёт: таймеры срабатывают по порядку, и каждый видит на часах свой момент.
    `sleep` — сон ноутбука, как его видят настоящие часы: время на стене перескакивает, а таймеры
    (`threading.Timer` ждёт по монотонному времени, которое во сне стоит) дожидаются своего остатка
    уже после пробуждения. `set` — скачок, после которого опоздавшие таймеры срабатывают сразу.
    """

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
        # По одному и вне замка: сработавший таймер может завести новый или обратиться к часам.
        while (timer := self._pop_due(target)) is not None:
            self._now = max(self._now, timer.moment)
            if not timer.cancelled:
                timer.callback()
        self._now = target

    def sleep(self, seconds: float) -> None:
        """Сон ноутбука на `seconds`: время перескакивает, все таймеры сдвигаются на столько же."""
        shift = timedelta(seconds=seconds)
        with self._lock:
            for timer in self._timers:
                timer.moment += shift
            self._now += shift

    def set(self, moment: datetime) -> None:
        """Время перескакивает на `moment`; таймеры, чей момент прошёл, срабатывают уже после."""
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
