"""Нарезка звука Источника на Реплики по паузам (ADR-0002).

Детектор речи смотрит на звук кадрами и отвечает, насколько кадр похож на речь; по его ответам
нарезчик находит речь и паузы. Параметры — `RecognitionSettings`, умолчания — итоги замера (задача 01):

- речь начинается, когда вероятность речи дошла до порога, и кончается, когда упала ниже порога − 0,15;
- Реплика закрывается, когда после речи тишина длится `min_silence_duration_ms` + 2 × `speech_pad_ms`
  (0,7 с по умолчанию): короткие паузы внутри фразы её не рвут — короче необходимого не режем;
- к краям Реплики добавляется `speech_pad_ms` звука — сколько есть и не залезая в предыдущую Реплику;
- речь короче `min_speech_duration_ms` (щелчок, вздох) Репликой не считается, а тишина в распознавание
  не идёт вовсе;
- Реплика, доросшая до `max_utterance_seconds`, режется принудительно по последней паузе не короче
  `forced_cut_min_pause_ms`, а если такой паузы нет — жёстко по пределу;
- «Пауза» и «Стоп» закрывают текущую Реплику (`flush`).

Время отрезка — от начала захвата Источника, Паузы Сессии в него не входят.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from spook.audio import SAMPLE_RATE, AudioSegment, Source
from spook.settings import RecognitionSettings

END_OF_SPEECH_MARGIN = 0.15  # речь кончается, когда вероятность ниже порога на столько (как в onnx-asr)


class SpeechDetector(Protocol):
    """Детектор речи одного потока звука: кадр → вероятность речи от 0 до 1.

    Кадр — `frame_samples` отсчётов float32, моно, 16 кГц; кадры идут подряд, и детектор может помнить
    предыдущие (у Silero это состояние сети), поэтому у каждого Источника Сессии свой детектор.
    """

    @property
    def frame_samples(self) -> int: ...

    def speech_probability(self, frame: np.ndarray) -> float: ...


SpeechDetectorFactory = Callable[[], SpeechDetector]
"""Создаёт детектор для нового потока звука — по одному на Источник Сессии."""


@dataclass
class _OpenUtterance:
    """Незакрытая Реплика; позиции — номера отсчётов от начала захвата."""

    start: int  # начало вместе с запасом
    speech_start: int
    speech_end: int  # конец последней речи
    pauses: list[tuple[int, int]] = field(default_factory=list)  # паузы между речью внутри Реплики


def _samples(milliseconds: float) -> int:
    return int(round(milliseconds * SAMPLE_RATE / 1000))


class UtteranceCutter:
    """Принимает звук одного Источника порциями и отдаёт закрытые Реплики отрезками звука."""

    def __init__(self, source: Source, settings: RecognitionSettings, detector: SpeechDetector) -> None:
        self._source = source
        self._detector = detector
        self._frame = detector.frame_samples
        self._start_threshold = settings.speech_threshold
        self._end_threshold = settings.speech_threshold - END_OF_SPEECH_MARGIN
        self._pad = _samples(settings.speech_pad_ms)
        self._closing_silence = _samples(settings.min_silence_duration_ms) + 2 * self._pad
        self._min_speech = _samples(settings.min_speech_duration_ms)
        self._min_forced_pause = _samples(settings.forced_cut_min_pause_ms)
        self._max = int(round(settings.max_utterance_seconds * SAMPLE_RATE))
        self._buffer = np.empty(0, dtype=np.float32)
        self._base = 0  # позиция первого отсчёта буфера
        self._checked = 0  # до этой позиции звук уже показан детектору
        self._free_from = 0  # раньше звук уже отдан в Реплики: следующая начнётся не раньше
        self._speaking = False
        self._utterance: _OpenUtterance | None = None

    def push(self, samples: np.ndarray) -> list[AudioSegment]:
        """Принимает порцию звука и возвращает закрытые Реплики (возможно, ни одной)."""
        self._buffer = np.concatenate([self._buffer, np.asarray(samples, dtype=np.float32)])
        segments: list[AudioSegment] = []
        while self._checked + self._frame <= self._end:
            position = self._checked
            frame = self._buffer[position - self._base : position + self._frame - self._base]
            self._checked += self._frame
            self._on_frame(position, self._detector.speech_probability(frame), segments)
            self._cut_if_too_long(self._checked, segments)
        # Неполный кадр в конце детектор ещё не видел: считается, что речь или тишина продолжается.
        self._cut_if_too_long(self._end, segments)
        self._forget_used()
        return segments

    def flush(self) -> AudioSegment | None:
        """Закрывает текущую Реплику — при «Пауза» и «Стоп»."""
        segments: list[AudioSegment] = []
        utterance = self._utterance
        if utterance is not None:
            if self._speaking:
                utterance.speech_end = self._end
            self._close(min(utterance.speech_end + self._pad, self._end), segments)
        self._speaking = False
        self._checked = self._free_from = self._end
        self._forget_used()
        return segments[0] if segments else None

    @property
    def position(self) -> float:
        """Начало следующего отрезка в секундах: раньше него Источник уже ничего не даст —
        начало незакрытой Реплики, а без неё — самое раннее начало новой (с запасом перед речью)."""
        if self._utterance is not None:
            return self._utterance.start / SAMPLE_RATE
        return max(self._free_from, self._checked - self._pad) / SAMPLE_RATE

    @property
    def buffered_seconds(self) -> float:
        """Сколько звука накоплено и ещё может войти в Реплику."""
        return len(self._buffer) / SAMPLE_RATE

    @property
    def _end(self) -> int:
        return self._base + len(self._buffer)

    def _on_frame(self, position: int, probability: float, segments: list[AudioSegment]) -> None:
        frame_end = position + self._frame
        utterance = self._utterance
        if not self._speaking and probability >= self._start_threshold:
            self._speaking = True
            if utterance is None:
                start = max(position - self._pad, self._free_from)
                self._utterance = utterance = _OpenUtterance(start, speech_start=position, speech_end=frame_end)
            else:
                utterance.pauses.append((utterance.speech_end, position))
        elif self._speaking and probability < self._end_threshold:
            self._speaking = False
        if utterance is None:
            return
        if self._speaking:
            utterance.speech_end = frame_end
        elif frame_end - utterance.speech_end >= self._closing_silence:
            self._close(utterance.speech_end + self._pad, segments)

    def _cut_if_too_long(self, reached: int, segments: list[AudioSegment]) -> None:
        """Принудительный разрез Реплики, доросшей до предела: по последней паузе, без неё — жёстко."""
        while (utterance := self._utterance) is not None and reached - utterance.start >= self._max:
            limit = utterance.start + self._max
            current_pause = min(self._checked, limit) - utterance.speech_end
            if not self._speaking and current_pause >= self._min_forced_pause:
                self._close(min(utterance.speech_end + self._pad, limit), segments)
                continue
            pause = next(
                (pause for pause in reversed(utterance.pauses) if pause[1] - pause[0] >= self._min_forced_pause),
                None,
            )
            if pause is not None:
                begin, end = pause
                middle = (begin + end) // 2
                cut, next_start, next_speech = min(begin + self._pad, middle), max(end - self._pad, middle), end
            else:
                cut = next_start = next_speech = limit
            segments.append(self._segment(utterance.start, cut))
            self._free_from = cut
            self._utterance = _OpenUtterance(
                next_start,
                speech_start=next_speech,
                speech_end=max(utterance.speech_end, next_speech),
                pauses=[later for later in utterance.pauses if later[0] >= next_speech],
            )

    def _close(self, end: int, segments: list[AudioSegment]) -> None:
        utterance = self._utterance
        assert utterance is not None
        self._utterance = None
        if utterance.speech_end - utterance.speech_start >= self._min_speech:
            segments.append(self._segment(utterance.start, end))
            self._free_from = end

    def _segment(self, start: int, end: int) -> AudioSegment:
        samples = self._buffer[start - self._base : end - self._base].copy()
        return AudioSegment(source=self._source, samples=samples, start=start / SAMPLE_RATE)

    def _forget_used(self) -> None:
        """Отбрасывает звук, который больше не понадобится: отданный и тишину старше запаса."""
        if self._utterance is not None:
            keep_from = self._utterance.start
        else:
            keep_from = max(self._free_from, self._checked - self._pad)
        keep_from = min(keep_from, self._checked)
        if keep_from > self._base:
            self._buffer = self._buffer[keep_from - self._base :]
            self._base = keep_from
