"""Служба с настоящим GigaAM: в простое модель не загружена, после первого «Старт» — загружена,
и Реплики Лекции попадают в Заметку распознанным текстом.

Медленный тест на главном шве: вход звука — образец речи (не захваченный звук, ADR-0001),
распознаватель — настоящий. Без модели или образца тест пропускается.
"""

from __future__ import annotations

import logging

import pytest

from speech_samples import EXPECTED, model_settings, read_sample, similarity
from spook.recognizer import GigaamRecognizer, Recognizer

pytestmark = pytest.mark.slow

SAMPLE = "utt_10s.wav"


@pytest.fixture
def recognizer() -> Recognizer:
    return GigaamRecognizer(model_settings())


def test_model_is_loaded_only_after_first_start_and_lecture_gets_recognized_text(spook, caplog):
    caplog.set_level(logging.INFO, logger="spook")
    samples = read_sample(SAMPLE)
    assert spook.page().status_code == 200

    assert not spook.recognizer.loaded, "модель загружена в простое"

    assert spook.start_lecture().status_code == 200
    spook.audio.feed(samples)
    assert spook.stop().status_code == 200
    note = spook.lectures_dir / "2026-11-06 14-02 Лекция.md"
    spook.wait_until(lambda: "XIX" in note.read_text(encoding="utf-8"), timeout=60)

    assert spook.recognizer.loaded
    transcript = note.read_text(encoding="utf-8").split("## Транскрипт", 1)[1]
    paragraph = transcript.strip().split(" ", 1)[1]  # без отметки времени «**00:00**»
    assert similarity(EXPECTED[SAMPLE], paragraph) >= 0.9, transcript
    assert "Модель распознавания загружается" in caplog.text
