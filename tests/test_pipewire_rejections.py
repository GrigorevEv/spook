"""«Старт» на настоящем входе отклоняется с понятной причиной, когда PipeWire не даёт устройства.

PipeWire здесь не нужен: внешняя программа `pw-dump` подменена скриптом-заглушкой на PATH.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spook.pipewire import PipeWireAudioInput

# Дамп PipeWire без устройства вывода: есть только микрофон.
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
def program_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Единственный каталог на PATH: сюда кладутся заглушки внешних программ."""
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.fixture
def audio_input(program_dir: Path) -> PipeWireAudioInput:
    return PipeWireAudioInput()


def stub_pw_dump(program_dir: Path, output: str) -> None:
    script = program_dir / "pw-dump"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    script.chmod(0o755)


def test_start_is_rejected_when_there_is_no_default_output_device(spook, program_dir: Path):
    stub_pw_dump(program_dir, DUMP_WITHOUT_OUTPUT_DEVICE)

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "Нет устройства для захвата: Системный звук" in response.text
    assert "нет Сессии" in spook.page().text


def test_start_is_rejected_when_pipewire_is_unavailable(spook):
    response = spook.start_lecture()

    assert response.status_code == 409
    assert "PipeWire недоступен" in response.text


def test_start_is_rejected_when_pinned_device_is_absent(spook, program_dir: Path):
    stub_pw_dump(program_dir, DUMP_WITHOUT_OUTPUT_DEVICE)
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[sources]\nmic = "alsa_input.usb-mic"\nsystem = "alsa_output.usb-dac"\n')

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "Нет устройства для захвата: Системный звук" in response.text
    assert "закреплённое устройство «alsa_output.usb-dac» не найдено" in response.text
    assert "нет Сессии" in spook.page().text
