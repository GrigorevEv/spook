from __future__ import annotations

import logging

import pytest

from speech_samples import RECOGNIZED_IN_BENCHMARK, model_settings, read_sample, similarity
from spook.recognizer import GigaamRecognizer, Recognizer

pytestmark = pytest.mark.slow

SAMPLE = "utt_10s.wav"


@pytest.fixture
def recognizer() -> Recognizer:
    return GigaamRecognizer(model_settings())


def test_model_is_loaded_only_after_first_start_and_meeting_gets_recognized_text(spook, caplog):
    caplog.set_level(logging.INFO, logger="spook")
    samples = read_sample(SAMPLE)
    assert spook.page().status_code == 200

    assert not spook.recognizer.loaded, "модель загружена в простое"

    assert spook.start_meeting().status_code == 200
    spook.feed_system(samples)
    assert spook.stop().status_code == 200
    spook.wait_until(lambda: "XIX" in spook.note_page().transcript, timeout=60)

    assert spook.recognizer.loaded
    transcript = spook.note_page().transcript
    paragraph_without_time = transcript.split("Собеседники: ", 1)[1]
    assert similarity(RECOGNIZED_IN_BENCHMARK[SAMPLE], paragraph_without_time) >= 0.9, transcript
    assert "Модель распознавания загружается" in caplog.text
