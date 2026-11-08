"""Источники идущей Сессии: название устройства и уровень сигнала идут потоком событий и видны на странице."""

from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import SAMPLE_RATE


def sine(seconds: float, amplitude: float, frequency: float = 440.0) -> np.ndarray:
    """Синус заданной амплитуды: его RMS = амплитуда/√2, пик = амплитуда."""
    moments = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * frequency * moments)).astype(np.float32)


def test_state_during_session_names_source_and_its_device(spook):
    spook.start_lecture()

    with spook.events() as events:
        state = events.next_event()

    assert state.name == "state"
    assert 'data-source="system"' in state.data
    assert "Системный звук" in state.data
    assert "образцы" in state.data
    assert "образцы" in spook.page().text

    assert spook.pause().status_code == 200
    assert "образцы" in spook.page().text, "на Паузе название устройства остаётся на странице"


def test_levels_event_carries_rms_and_peak_of_source_signal(spook):
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("state")  # страница получила состояние — значит, подписана и на уровни
        spook.audio.feed(sine(0.5, amplitude=0.5))
        levels = events.wait_for("levels")

    data = json.loads(levels.data)
    assert data["system"]["rms"] == pytest.approx(0.3536, abs=0.001)
    assert data["system"]["peak"] == pytest.approx(0.5, abs=0.001)


def test_silence_gives_zero_level(spook):
    spook.start_lecture()

    with spook.events() as events:
        events.wait_for("state")
        spook.audio.feed(np.zeros(SAMPLE_RATE // 10, dtype=np.float32))
        levels = events.wait_for("levels")

    assert json.loads(levels.data) == {"system": {"rms": 0.0, "peak": 0.0}}
