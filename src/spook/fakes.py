"""Подмены внешнего мира для тестов и запуска «на подменах»: вход звука из образцов, детектор речи
по громкости и сценарный распознаватель."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from spook.audio import SAMPLE_RATE, AudioSegment, AudioSink, CaptureUnavailable, Device, DeviceChange, DeviceSink, Level, Source

DEFAULT_DEVICE = "образцы"


@dataclass
class FakeCapture:
    source: Source
    device_name: str = DEFAULT_DEVICE
    pinned: str = ""  # закреплённое устройство; пустое — захват следует за устройством по умолчанию
    active: bool = True
    sink: AudioSink = field(default=lambda samples: None, repr=False)
    on_device_change: DeviceSink = field(default=lambda change: None, repr=False)

    def stop(self) -> None:
        self.active = False


class FakeAudioInput:
    """Вход звука, в который звук подаётся вручную: `feed` отдаёт порцию идущему захвату Источника.

    Источники из `unavailable` считаются устройствами, которых нет: их захват начать нельзя.
    События устройств разыгрываются так же вручную: `change_default` — у Источника сменилось устройство
    по умолчанию, `lose_device` — устройство пропало. Названия устройств — любые строки.
    Список устройств для закрепления (`devices`) тест задаёт в `known_devices`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._captures: dict[Source, FakeCapture] = {}
        self._defaults: dict[Source, str] = {source: DEFAULT_DEVICE for source in Source}
        self.unavailable: set[Source] = set()
        self.known_devices: dict[Source, list[Device]] = {source: [] for source in Source}

    def start(self, source: Source, sink: AudioSink, on_device_change: DeviceSink, pinned: str = "") -> FakeCapture:
        if source in self.unavailable:
            raise CaptureUnavailable(f"Нет устройства для захвата: {source.label}")
        capture = FakeCapture(source, pinned or self._defaults[source], pinned, sink=sink, on_device_change=on_device_change)
        with self._lock:
            self._captures[source] = capture
        return capture

    def devices(self, source: Source) -> list[Device]:
        return list(self.known_devices[source])

    @property
    def active_sources(self) -> set[Source]:
        """Источники, захват которых идёт сейчас."""
        with self._lock:
            return {source for source, capture in self._captures.items() if capture.active}

    def feed(self, samples: np.ndarray, source: Source = Source.SYSTEM) -> None:
        """Подаёт звук; без идущего захвата звук пропадает, как в настоящей системе."""
        with self._lock:
            capture = self._captures.get(source)
        if capture is None or not capture.active:
            return
        capture.sink(samples)

    def change_default(self, source: Source, device_name: str) -> None:
        """Устройство Источника по умолчанию сменилось: незакреплённый захват переходит на него."""
        with self._lock:
            self._defaults[source] = device_name
            capture = self._captures.get(source)
            if capture is None or not capture.active or capture.pinned or capture.device_name == device_name:
                return
            capture.device_name = device_name
        capture.on_device_change(DeviceChange(source, device_name))

    def lose_device(self, device_name: str, new_default: str = "") -> None:
        """Устройство пропало: захваты с него переходят на устройство Источника по умолчанию и дальше
        следуют за ним, даже если были закреплены. Если пропало само умолчание, новым умолчанием
        становится `new_default`; пустое — устройства у Источника больше нет."""
        changes: list[DeviceChange] = []
        with self._lock:
            for source in Source:
                if self._defaults[source] == device_name:
                    self._defaults[source] = new_default
                capture = self._captures.get(source)
                if capture is None or not capture.active or capture.device_name != device_name:
                    continue
                capture.pinned = ""
                capture.device_name = self._defaults[source]
                changes.append(DeviceChange(source, capture.device_name, lost=True))
        for change in changes:
            self._captures[change.source].on_device_change(change)


class LoudnessSpeechDetector:
    """Детектор речи на подменах: кадр 10 мс громче порога — речь, тише — тишина.

    Образцы «речи» на подменах — шум, тишина — нули, так что нарезка по паузам идёт без модели.
    """

    frame_samples = SAMPLE_RATE // 100

    def __init__(self, loudness: float = 0.01) -> None:
        self._loudness = loudness

    def speech_probability(self, frame: np.ndarray) -> float:
        return 1.0 if Level.of(frame).rms >= self._loudness else 0.0


@dataclass(frozen=True)
class Heard:
    """Что получил распознаватель: без самого звука, только границы отрезка."""

    source: Source
    start: float
    duration: float


class ScriptedRecognizer:
    """Сценарный распознаватель: заданные тексты по порядку отрезков; пустой текст — «тишина».

    Когда сценарий исчерпан, отрезки считаются тишиной. Медленный распознаватель изображается
    «воротами»: после `hold` каждый отрезок ждёт, пока тест не пропустит его через `release`,
    так что тест управляет отставанием, не дожидаясь реального времени.
    """

    def __init__(self, *texts: str) -> None:
        self._texts: deque[str] = deque(texts)
        self._gate = threading.Condition()
        self._held = False
        self._passes = 0  # сколько отрезков пропустить через закрытые ворота
        self.heard: list[Heard] = []

    def say(self, *texts: str) -> None:
        with self._gate:
            self._texts.extend(texts)

    def hold(self) -> None:
        """Распознаватель «задумывается»: отрезки ждут у ворот, пока их не пропустит `release`."""
        with self._gate:
            self._held = True
            self._passes = 0

    def release(self, segments: int = 1) -> None:
        """Пропускает через ворота столько отрезков."""
        with self._gate:
            self._passes += segments
            self._gate.notify_all()

    def release_all(self) -> None:
        """Открывает ворота насовсем: ждущие и будущие отрезки распознаются сразу."""
        with self._gate:
            self._held = False
            self._gate.notify_all()

    def recognize(self, segment: AudioSegment) -> str:
        with self._gate:
            while self._held and self._passes == 0:
                self._gate.wait()
            if self._held:
                self._passes -= 1
            self.heard.append(Heard(segment.source, segment.start, segment.duration))
            return self._texts.popleft() if self._texts else ""
