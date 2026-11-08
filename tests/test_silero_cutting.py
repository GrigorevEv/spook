"""Нарезка на Реплики настоящим детектором Silero VAD — главный шов на образцах речи задачи 01.

Запуск: `uv run pytest -q -m slow`. Образцы — открытая речь Russian LibriSpeech, склеенная с паузами
(`scripts/gigaam-bench/prepare_samples.py`); лежат вне репозитория
в `~/.local/share/spook/bench/samples`. Модель — `~/.local/share/spook/models/silero-vad` или копия замера.
Без образцов или модели тесты пропускаются. Звук только читается, на диск ничего не пишется.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest

from conftest import SAMPLE_RATE
from spook.silero import MODEL_FILE, SileroSpeechDetection, default_model_dir

pytestmark = pytest.mark.slow

BENCH_DIR = Path(os.environ.get("SPOOK_BENCH_DIR", "~/.local/share/spook/bench")).expanduser()
SAMPLES_DIR = BENCH_DIR / "samples"
MODEL_DIRS = (default_model_dir(), BENCH_DIR / "models" / "onnx-asr" / "silero-vad")
CHUNK_SECONDS = 0.1  # порциями, как приходит звук из захвата
TOLERANCE_SECONDS = 0.3  # детектор видит кадрами по 32 мс и добавляет запас 0,1 с


@pytest.fixture
def speech_detector() -> SileroSpeechDetection:
    model_dir = next((folder for folder in MODEL_DIRS if (folder / MODEL_FILE).is_file()), None)
    if model_dir is None:
        pytest.skip(f"модели Silero VAD нет ни в одном из каталогов: {', '.join(map(str, MODEL_DIRS))}")
    return SileroSpeechDetection(model_dir)


def sample(name: str) -> tuple[np.ndarray, dict]:
    """Образец задачи 01: звук и его описание из `samples.json` (фразы и их границы)."""
    path = SAMPLES_DIR / name
    if not path.is_file():
        pytest.skip(f"нет образца {path}: его готовит prepare_samples.py задачи 01")
    with wave.open(str(path), "rb") as file:
        assert (file.getframerate(), file.getnchannels(), file.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        pcm = np.frombuffer(file.readframes(file.getnframes()), dtype="<i2")
    manifest = json.loads((SAMPLES_DIR / "samples.json").read_text(encoding="utf-8"))
    return pcm.astype(np.float32) / 32768.0, manifest[name]["source"]


def feed(spook, samples: np.ndarray) -> None:
    step = int(CHUNK_SECONDS * SAMPLE_RATE)
    for offset in range(0, len(samples), step):
        spook.audio.feed(samples[offset : offset + step])


def test_phrase_pause_phrase_from_real_speech_gives_utterances_at_phrase_boundaries(spook):
    audio, source = sample("ppp_0700ms.wav")  # три фразы, паузы между ними 0,7 с
    phrases = source["phrases"]
    spook.recognizer.say(*(f"Фраза {number}." for number in range(1, len(phrases) + 1)))
    spook.start_lecture()

    feed(spook, np.concatenate([audio, np.zeros(SAMPLE_RATE, dtype=np.float32)]))
    spook.wait_until(lambda: len(spook.recognizer.heard) == len(phrases), timeout=10)

    for utterance, phrase in zip(spook.recognizer.heard, phrases, strict=True):
        assert abs(utterance.start - phrase["start"]) <= TOLERANCE_SECONDS, (utterance, phrase)
        assert abs(utterance.start + utterance.duration - phrase["end"]) <= TOLERANCE_SECONDS, (utterance, phrase)


def test_pause_of_half_a_second_does_not_cut_real_speech(spook):
    audio, _ = sample("ppp_0500ms.wav")  # те же три фразы, паузы 0,5 с — короче паузы закрытия 0,7 с
    spook.start_lecture()

    feed(spook, np.concatenate([audio, np.zeros(SAMPLE_RATE, dtype=np.float32)]))
    spook.wait_until(lambda: len(spook.recognizer.heard) >= 1, timeout=10)
    spook.stop()
    spook.wait_until(lambda: "нет Сессии" in spook.page().text, timeout=10)

    assert len(spook.recognizer.heard) == 1


def test_long_real_speech_without_pauses_is_cut_before_20_seconds_between_sentences(spook):
    audio, source = sample("speech_45s.wav")  # 48 с: предложения подряд с зазором 0,1 с
    sentence_ends = [part["end"] for part in source["glued"]]
    spook.start_lecture()

    feed(spook, audio)
    spook.stop()
    spook.wait_until(lambda: "нет Сессии" in spook.page().text, timeout=10)

    heard = spook.recognizer.heard
    assert len(heard) >= 3
    assert all(utterance.duration <= 20.0 for utterance in heard)
    for before, after in zip(heard, heard[1:]):
        cut = before.start + before.duration
        assert after.start >= cut  # ни одна часть звука не распознаётся дважды
        assert min(abs(cut - end) for end in sentence_ends) <= TOLERANCE_SECONDS, (cut, sentence_ends)
