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
    note = spook.meetings_dir / "2026-11-06 14-02 Встреча.md"
    spook.wait_until(lambda: "XIX" in note.read_text(encoding="utf-8"), timeout=60)

    assert spook.recognizer.loaded
    transcript = note.read_text(encoding="utf-8").split("## Транскрипт", 1)[1]
    paragraph_without_time = transcript.strip().split(":** ", 1)[1]
    assert similarity(RECOGNIZED_IN_BENCHMARK[SAMPLE], paragraph_without_time) >= 0.9, transcript
    assert "Модель распознавания загружается" in caplog.text
