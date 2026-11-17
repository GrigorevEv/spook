from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from spook.audio import AudioSink, Capture, DeviceSink, Source
from spook.fakes import DEFAULT_DEVICE, FakeAudioInput

from conftest import SAMPLE_RATE

CAPTURE_STOP_TIMEOUT = 4.0
AT_ONCE = 2.0


class StreamingCapture:
    device_name = DEFAULT_DEVICE

    def __init__(self, sink: AudioSink) -> None:
        self._stopping = threading.Event()
        streaming = threading.Event()

        def deliver() -> None:
            streaming.set()
            sink(np.zeros(SAMPLE_RATE // 10, dtype=np.float32))
            self._stopping.wait()

        self._thread = threading.Thread(target=deliver, name="test-system-sound", daemon=True)
        self._thread.start()
        streaming.wait()

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=CAPTURE_STOP_TIMEOUT)


class StreamingSystemSound(FakeAudioInput):
    def start(self, source: Source, sink: AudioSink, on_device_change: DeviceSink, pinned: str = "") -> Capture:
        if source is Source.SYSTEM:
            return StreamingCapture(sink)
        return super().start(source, sink, on_device_change, pinned)


@pytest.fixture
def audio_input() -> StreamingSystemSound:
    return StreamingSystemSound()


def test_start_of_meeting_without_microphone_is_rejected_at_once_while_system_sound_streams(spook):
    spook.audio.unavailable.add(Source.MIC)

    began = time.monotonic()
    response = spook.start_meeting()
    elapsed = time.monotonic() - began

    assert response.status_code == 409 and "Микрофон" in response.text
    assert elapsed < AT_ONCE, f"отказ пришёл через {elapsed:.1f} с"


def test_resume_of_meeting_without_microphone_is_rejected_at_once_while_system_sound_streams(spook):
    assert spook.start_meeting().status_code == 200
    assert spook.pause().status_code == 200
    spook.audio.unavailable.add(Source.MIC)

    began = time.monotonic()
    response = spook.resume()
    elapsed = time.monotonic() - began

    assert response.status_code == 409 and "Микрофон" in response.text
    assert elapsed < AT_ONCE, f"отказ пришёл через {elapsed:.1f} с"
