"""Вход звука: Источники, отрезки звука в памяти, уровень сигнала и протокол захвата.

Звук живёт только в памяти — ни файлов, ни временных файлов (ADR-0001).
Настоящий вход из PipeWire — `spook.pipewire`; подмена для тестов — `spook.fakes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol

import numpy as np

SAMPLE_RATE = 16_000


class Source(StrEnum):
    SYSTEM = "system"
    MIC = "mic"

    @property
    def label(self) -> str:
        return {Source.SYSTEM: "Системный звук", Source.MIC: "Микрофон"}[self]


@dataclass(frozen=True)
class AudioSegment:
    """Отрезок звука одного Источника: float32, моно, 16 кГц; `start` — секунды от начала захвата."""

    source: Source
    samples: np.ndarray
    start: float

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


AudioSink = Callable[[np.ndarray], None]
"""Получатель порций звука (float32, моно, 16 кГц); вызывается из потока захвата."""


@dataclass(frozen=True)
class Level:
    """Уровень сигнала порции звука: RMS и пик в долях полной шкалы (0…1)."""

    rms: float
    peak: float

    @classmethod
    def of(cls, samples: np.ndarray) -> Level:
        if len(samples) == 0:
            return cls(0.0, 0.0)
        values = np.asarray(samples, dtype=np.float32)
        return cls(
            rms=float(np.sqrt(np.mean(np.square(values, dtype=np.float64)))),
            peak=float(np.max(np.abs(values))),
        )


@dataclass(frozen=True)
class DeviceChange:
    """Захват Источника перешёл на другое устройство: вслед за сменой устройства по умолчанию или
    из-за пропажи прежнего (`lost`). `device_name` — название нового устройства; пустое — устройства
    нет, и захват ждёт его появления."""

    source: Source
    device_name: str
    lost: bool = False


DeviceSink = Callable[[DeviceChange], None]
"""Получатель событий устройств захвата; вызывается из потока захвата."""


class CaptureUnavailable(Exception):
    """Захват Источника начать нельзя; текст — причина для пользователя."""


@dataclass(frozen=True)
class Device:
    """Устройство, с которого можно захватывать Источник: `name` — для закрепления в настройках
    (для PipeWire — `node.name`), `description` — для страницы."""

    name: str
    description: str


class Capture(Protocol):
    """Идущий захват одного Источника; сам следует за устройством и сообщает о переходах в `DeviceSink`."""

    @property
    def device_name(self) -> str: ...

    def stop(self) -> None: ...


class AudioInput(Protocol):
    """Вход звука: начинает захват Источника и отдаёт звук порциями в `sink`.

    `pinned` — закреплённое устройство Источника (для PipeWire — `node.name`); пустое — устройство
    по умолчанию, за сменой которого захват следует. О переходе на другое устройство захват сообщает
    в `on_device_change`. Без устройства — `CaptureUnavailable` с причиной для пользователя.
    `devices` перечисляет устройства Источника для закрепления.
    """

    def start(self, source: Source, sink: AudioSink, on_device_change: DeviceSink, pinned: str = "") -> Capture: ...

    def devices(self, source: Source) -> list[Device]:
        """Устройства, на которых можно закрепить Источник (экран «Настройки»); без связи со звуковой
        системой — `CaptureUnavailable` с причиной."""
        ...
