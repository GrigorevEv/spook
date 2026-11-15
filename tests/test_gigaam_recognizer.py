from __future__ import annotations

import numpy as np
import pytest

from speech_samples import RECOGNIZED_IN_BENCHMARK, model_settings, read_sample, similarity
from spook.audio import SAMPLE_RATE, AudioSegment, Source
from spook.recognizer import GigaamRecognizer

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def gigaam() -> GigaamRecognizer:
    return GigaamRecognizer(model_settings())


@pytest.mark.parametrize("sample", sorted(RECOGNIZED_IN_BENCHMARK))
def test_russian_speech_sample_is_recognized_as_text_with_punctuation(gigaam, sample):
    segment = AudioSegment(Source.SYSTEM, read_sample(sample), start=0.0)

    text = gigaam.recognize(segment)

    assert similarity(RECOGNIZED_IN_BENCHMARK[sample], text) >= 0.9, text
    assert text[0].isupper() and text.endswith("."), text
    assert "," in text or "%" in text, text


def test_silence_is_recognized_as_empty_text(gigaam):
    segment = AudioSegment(Source.SYSTEM, np.zeros(2 * SAMPLE_RATE, dtype=np.float32), start=0.0)

    assert gigaam.recognize(segment).strip() == ""
