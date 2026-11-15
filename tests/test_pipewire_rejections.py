from __future__ import annotations

import json
from pathlib import Path

import pytest

from spook.pipewire import PipeWireAudioInput

DUMP_WITHOUT_OUTPUT_DEVICE = json.dumps(
    [
        {"id": 0, "type": "PipeWire:Interface:Core", "info": {"props": {"core.name": "pipewire-0"}}},
        {
            "id": 36,
            "type": "PipeWire:Interface:Metadata",
            "props": {"metadata.name": "default"},
            "metadata": [
                {
                    "subject": 0,
                    "key": "default.audio.source",
                    "type": "Spa:String:JSON",
                    "value": {"name": "alsa_input.usb-mic"},
                }
            ],
        },
        {
            "id": 50,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "node.name": "alsa_input.usb-mic",
                    "node.description": "Микрофон USB",
                    "media.class": "Audio/Source",
                }
            },
        },
    ]
)


@pytest.fixture
def only_dir_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.fixture
def audio_input(only_dir_on_path: Path) -> PipeWireAudioInput:
    return PipeWireAudioInput()


def stub_pw_dump(only_dir_on_path: Path, output: str) -> None:
    script = only_dir_on_path / "pw-dump"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    script.chmod(0o755)


def test_start_is_rejected_when_there_is_no_default_output_device(spook, only_dir_on_path: Path):
    stub_pw_dump(only_dir_on_path, DUMP_WITHOUT_OUTPUT_DEVICE)

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "Нет устройства для захвата: Системный звук" in response.text
    assert "нет Сессии" in spook.page().text


def test_start_is_rejected_when_pipewire_is_unavailable(spook):
    response = spook.start_lecture()

    assert response.status_code == 409
    assert "PipeWire недоступен" in response.text


def test_start_is_rejected_when_pinned_device_is_absent(spook, only_dir_on_path: Path):
    stub_pw_dump(only_dir_on_path, DUMP_WITHOUT_OUTPUT_DEVICE)
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[sources]\nmic = "alsa_input.usb-mic"\nsystem = "alsa_output.usb-dac"\n')

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "Нет устройства для захвата: Системный звук" in response.text
    assert "закреплённое устройство «alsa_output.usb-dac» не найдено" in response.text
    assert "нет Сессии" in spook.page().text
