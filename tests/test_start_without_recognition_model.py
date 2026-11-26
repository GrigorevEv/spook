from __future__ import annotations

from pathlib import Path

import pytest

from spook.recognizer import GigaamRecognizer, Recognizer
from spook.settings import RecognitionSettings
from test_prepare import put_recognition_model


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "spook" / "models" / "gigaam-v3-e2e-rnnt"


@pytest.fixture
def recognizer(model_dir: Path) -> Recognizer:
    return GigaamRecognizer(RecognitionSettings(model_dir=model_dir))


def test_start_without_recognition_model_is_rejected_with_reason_and_leaves_no_session(spook, model_dir):
    meeting = spook.start_meeting()

    reason = f">Модель распознавания не скачана в {model_dir}: выполните `spook prepare`<"
    assert meeting.status_code == 409, meeting.text
    assert reason in meeting.text
    assert "нет Сессии" in spook.page().text
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))
    assert spook.audio.active_sources == set()


def test_start_after_spook_prepare_begins_session_without_restart_and_does_not_load_model(spook, model_dir):
    assert spook.start_meeting().status_code == 409
    put_recognition_model(model_dir)

    response = spook.start_meeting()

    assert response.status_code == 200, response.text
    assert "идёт" in spook.page().text
    assert not spook.recognizer.loaded, "модель загружена при «Старт»"
