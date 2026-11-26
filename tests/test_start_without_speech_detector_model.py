from __future__ import annotations

import ipaddress
import shutil
import socket
from pathlib import Path

import pytest

from spook.cutting import SpeechDetectorFactory
from spook.silero import MODEL_FILE, SileroSpeechDetection
from test_silero_cutting import MODEL_DIRS


def is_loopback(host: object) -> bool:
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


@pytest.fixture
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []
    resolve = socket.getaddrinfo

    def resolve_only_loopback(host, *args, **kwargs):
        if not is_loopback(host):
            attempts.append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, "сеть в тестах недоступна")
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve_only_loopback)
    return attempts


@pytest.fixture
def speech_detector_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data" / "spook" / "models" / "silero-vad"


@pytest.fixture
def speech_detector(speech_detector_model_dir: Path, network_attempts: list[str]) -> SpeechDetectorFactory:
    return SileroSpeechDetection()


def test_start_without_speech_detector_model_goes_to_no_network_and_creates_no_model_folder(
    spook, network_attempts, speech_detector_model_dir
):
    spook.start_meeting()

    assert network_attempts == []
    assert not speech_detector_model_dir.exists()


def test_start_without_speech_detector_model_is_rejected_with_its_folder_and_leaves_no_session(
    spook, speech_detector_model_dir
):
    meeting = spook.start_meeting()

    reason = f">Модель детектора речи не скачана в {speech_detector_model_dir}: выполните `spook prepare`<"
    assert meeting.status_code == 409
    assert reason in meeting.text
    page = spook.page().text
    assert "нет Сессии" in page and ">Старт</button>" in page
    assert spook.audio.active_sources == set()
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))
    assert not (spook.state_dir / "session.json").exists()


@pytest.mark.slow
def test_start_after_speech_detector_model_is_downloaded_begins_session_without_service_restart(
    spook, speech_detector_model_dir
):
    downloaded = next((folder / MODEL_FILE for folder in MODEL_DIRS if (folder / MODEL_FILE).is_file()), None)
    if downloaded is None:
        pytest.skip(f"модели Silero VAD нет ни в одном из каталогов: {', '.join(map(str, MODEL_DIRS))}")
    assert spook.start_meeting().status_code == 409

    speech_detector_model_dir.mkdir(parents=True)
    shutil.copyfile(downloaded, speech_detector_model_dir / MODEL_FILE)
    response = spook.start_meeting()

    assert response.status_code == 200
    assert "идёт" in spook.page().text
