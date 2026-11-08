"""Контрактные тесты настоящего распознавателя GigaAM-v3 (шов распознавателя, ADR-0002).

Медленные: запускаются отдельно (`uv run pytest -m slow`). Модель не скачивается: если её нет
в каталоге данных приложения, тесты пропускаются с подсказкой `spook prepare`; так же — без образцов.
"""

from __future__ import annotations

import numpy as np
import pytest

from speech_samples import EXPECTED, model_settings, read_sample, similarity
from spook.audio import SAMPLE_RATE, AudioSegment, Source
from spook.recognizer import GigaamRecognizer

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def gigaam() -> GigaamRecognizer:
    return GigaamRecognizer(model_settings())


@pytest.mark.parametrize("sample", sorted(EXPECTED))
def test_russian_speech_sample_is_recognized_as_text_with_punctuation(gigaam, sample):
    segment = AudioSegment(Source.SYSTEM, read_sample(sample), start=0.0)

    text = gigaam.recognize(segment)

    assert similarity(EXPECTED[sample], text) >= 0.9, text
    assert text[0].isupper() and text.endswith("."), text
    assert "," in text or "%" in text, text


def test_silence_is_recognized_as_empty_text(gigaam):
    segment = AudioSegment(Source.SYSTEM, np.zeros(2 * SAMPLE_RATE, dtype=np.float32), start=0.0)

    assert gigaam.recognize(segment).strip() == ""
