"""Образцы русской речи и модель для медленных тестов настоящего распознавателя.

Образцы — не захваченный звук (ADR-0001): RuLS (аудиокниги LibriVox) и синтез Piper из замера
задачи 01; лежат вне репозитория, в `~/.local/share/spook/bench/samples` (каталог меняется
переменной `SPOOK_BENCH_DIR`). Модель — в штатном каталоге данных приложения; тесты её не скачивают.
Чего нет — тест пропускается с подсказкой, откуда это взять.
"""

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

# Эталоны — вывод GigaAM-v3 e2e_rnnt int8 на этих образцах в замере задачи 01
# (`docs/research/gigaam-benchmark.md`, раздел «Пунктуация и числа»; `results/onnx-asr_int8_t4.json`).
EXPECTED = {
    "utt_10s.wav": "Да-с, умный человек XIX столетия должен и нравственно обязан быть существом "
    "по преимуществу бесхарактерным.",
    "synth_numbers.wav": "В 2024 году выручка выросла на 15% и достигла 3 250 000₽. "
    "Следующая встреча 14 марта в 10:30. Участников будет 7.",
}


def model_settings() -> RecognitionSettings:
    """Настройки распознавания по умолчанию; модель — в штатном каталоге данных приложения."""
    settings = RecognitionSettings()
    if not model_is_present(settings):
        pytest.skip(f"модель распознавания не скачана в {settings.model_dir}: выполните `uv run spook prepare`")
    return settings


def read_sample(name: str) -> np.ndarray:
    """Образец речи: float32, моно, 16 кГц."""
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
    """Похожесть текстов вместе с пунктуацией, терпимая к мелочам: регистр, ё/е и пробелы не считаются."""

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()

    return difflib.SequenceMatcher(None, norm(expected), norm(actual)).ratio()
