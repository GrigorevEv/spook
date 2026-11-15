from __future__ import annotations

import difflib
import os
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from spook.audio import SAMPLE_RATE
from spook.recognizer import model_is_present
from spook.settings import RecognitionSettings

SAMPLES_DIR = Path(os.environ.get("SPOOK_BENCH_DIR", "~/.local/share/spook/bench")).expanduser() / "samples"

RECOGNIZED_IN_BENCHMARK = {
    "utt_10s.wav": "Да-с, умный человек XIX столетия должен и нравственно обязан быть существом "
    "по преимуществу бесхарактерным.",
    "synth_numbers.wav": "В 2024 году выручка выросла на 15% и достигла 3 250 000₽. "
    "Следующая встреча 14 марта в 10:30. Участников будет 7.",
}


def model_settings() -> RecognitionSettings:
    settings = RecognitionSettings()
    if not model_is_present(settings):
        pytest.skip(f"модель распознавания не скачана в {settings.model_dir}: выполните `uv run spook prepare`")
    return settings


def read_sample(name: str) -> np.ndarray:
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(
            f"нет образца речи {path}: образцы готовит scripts/gigaam-bench/prepare_samples.py "
            "(команда — в README.md рядом)"
        )
    with wave.open(str(path), "rb") as file:
        assert (file.getframerate(), file.getnchannels(), file.getsampwidth()) == (SAMPLE_RATE, 1, 2)
        frames = file.readframes(file.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def similarity(expected: str, actual: str) -> float:
    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()

    return difflib.SequenceMatcher(None, norm(expected), norm(actual)).ratio()
