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
    source: Source
    samples: np.ndarray
    start: float

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


AudioSink = Callable[[np.ndarray], None]


@dataclass(frozen=True)
class Level:
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
    source: Source
    device_name: str
    lost: bool = False


DeviceSink = Callable[[DeviceChange], None]


class CaptureUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Device:
    name: str
    description: str


class Capture(Protocol):
    @property
    def device_name(self) -> str: ...

    def stop(self) -> None: ...


class AudioInput(Protocol):
    def start(self, source: Source, sink: AudioSink, on_device_change: DeviceSink, pinned: str = "") -> Capture: ...

    def devices(self, source: Source) -> list[Device]:
        ...
