"""Настоящий вход следует за устройством: смена умолчания и пропажа устройства — на подменённых программах PipeWire.

PipeWire здесь не нужен, умолчания пользователя не трогаются: `pw-dump`, `pw-record` и `pw-metadata`
подменены скриптами-заглушками на PATH. Заглушка `pw-dump` отдаёт дамп из файла, который тест меняет;
`pw-record` пишет тишину, пока его цель есть в дампе, а без неё завершается с «target not found», как
настоящий с `node.dont-reconnect=true`; `pw-metadata -m` печатает строки, которые тест дописывает в файл.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spook.pipewire import PipeWireAudioInput

SPEAKERS = ("alsa_output.speakers", "Колонки")
HEADPHONES = ("alsa_output.usb-headphones", "Наушники USB")


def dump(default: str, *sinks: tuple[str, str]) -> str:
    return json.dumps(
        [
            {
                "id": 36,
                "type": "PipeWire:Interface:Metadata",
                "props": {"metadata.name": "default"},
                "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON", "value": {"name": default}}],
            },
            *(
                {
                    "id": 50 + number,
                    "type": "PipeWire:Interface:Node",
                    "info": {"props": {"node.name": name, "node.description": description, "media.class": "Audio/Sink"}},
                }
                for number, (name, description) in enumerate(sinks)
            ),
        ]
    )


def metadata_line(default: str) -> str:
    return f"update: id:0 key:'default.audio.sink' value:'{{\"name\":\"{default}\"}}' type:'Spa:String:JSON'\n"


class PipeWireStub:
    """Состояние заглушек: дамп, строки слежения за умолчаниями и журнал целей `pw-record`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.dump_file = directory / "dump.json"
        self.metadata_file = directory / "metadata.log"
        self.records_file = directory / "records.log"
        self.records_file.touch()
        self.metadata_file.touch()

    def set_devices(self, default: str, *sinks: tuple[str, str]) -> None:
        temporary = self.dump_file.with_suffix(".tmp")
        temporary.write_text(dump(default, *sinks), encoding="utf-8")
        temporary.replace(self.dump_file)

    def announce_default(self, default: str) -> None:
        with self.metadata_file.open("a", encoding="utf-8") as file:
            file.write(metadata_line(default))

    def record_targets(self) -> list[str]:
        return self.records_file.read_text(encoding="utf-8").split()


@pytest.fixture
def pipewire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipeWireStub:
    directory = tmp_path / "bin"
    directory.mkdir()
    stub = PipeWireStub(directory)
    scripts = {
        "pw-dump": f'#!/bin/sh\nexec cat "{stub.dump_file}"\n',
        "pw-metadata": f'#!/bin/sh\nexec tail -n +1 -f "{stub.metadata_file}"\n',
        "pw-record": (
            "#!/bin/sh\n"
            'while [ "$1" != "--target" ]; do shift; done\n'
            'target="$2"\n'
            f'echo "$target" >> "{stub.records_file}"\n'
            f'while grep -q "\\"node.name\\": \\"$target\\"" "{stub.dump_file}"; do\n'
            "  head -c 3200 /dev/zero\n"
            "  sleep 0.1\n"
            "done\n"
            'echo "remote error: id=2 seq:32 res:-2 (No such file or directory): target not found" >&2\n'
            "exit 1\n"
        ),
    }
    for name, text in scripts.items():
        script = directory / name
        script.write_text(text, encoding="utf-8")
        script.chmod(0o755)
    # Заглушки впереди: настоящие программы PipeWire не вызываются, а sh-утилиты заглушкам нужны.
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    return stub


@pytest.fixture
def audio_input(pipewire: PipeWireStub) -> PipeWireAudioInput:
    return PipeWireAudioInput()


def test_capture_moves_to_new_default_output_device(spook, pipewire: PipeWireStub):
    pipewire.set_devices(SPEAKERS[0], SPEAKERS, HEADPHONES)
    assert spook.start_lecture().status_code == 200

    with spook.events() as events:
        events.wait_for("state", containing="Колонки")
        pipewire.set_devices(HEADPHONES[0], SPEAKERS, HEADPHONES)
        pipewire.announce_default(HEADPHONES[0])

        state = events.wait_for("state", containing="Наушники USB")
        mark = events.wait_for("mark", containing="переключён")
        events.wait_for("levels")

    assert "идёт" in state.data
    assert "<em>0:00:00 Системный звук переключён на «Наушники USB»</em>" in mark.data
    assert 'class="warning"' not in spook.page().text
    assert pipewire.record_targets() == [SPEAKERS[0], HEADPHONES[0]]


def test_pinned_capture_ignores_new_default(spook, pipewire: PipeWireStub):
    pipewire.set_devices(SPEAKERS[0], SPEAKERS, HEADPHONES)
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write(f'\n[sources]\nsystem = "{SPEAKERS[0]}"\n')
    assert spook.start_lecture().status_code == 200

    with spook.events() as events:
        events.wait_for("state", containing="Колонки")
        pipewire.set_devices(HEADPHONES[0], SPEAKERS, HEADPHONES)
        pipewire.announce_default(HEADPHONES[0])
        pipewire.announce_default(SPEAKERS[0])  # вернуть умолчание: ни одна из строк не должна сменить устройство
        for _ in range(10):
            assert events.wait_for("levels")

    assert "Наушники USB" not in spook.page().text
    assert pipewire.record_targets() == [SPEAKERS[0]]


def test_lost_output_device_leaves_mark_and_warning_and_capture_continues_from_default(spook, pipewire: PipeWireStub):
    pipewire.set_devices(SPEAKERS[0], SPEAKERS, HEADPHONES)
    assert spook.start_lecture().status_code == 200

    with spook.events() as events:
        events.wait_for("state", containing="Колонки")
        pipewire.set_devices(HEADPHONES[0], HEADPHONES)  # колонки пропали, умолчание — наушники

        state = events.wait_for("state", containing="Наушники USB")
        mark = events.wait_for("mark", containing="пропало")
        events.wait_for("levels")

    expected = "Системный звук: устройство «Колонки» пропало, захват продолжается с «Наушники USB»"
    assert "идёт" in state.data
    assert f"<em>0:00:00 {expected}</em>" in mark.data
    assert f'<p class="warning" role="alert">{expected}</p>' in spook.page().text
    assert pipewire.record_targets() == [SPEAKERS[0], HEADPHONES[0]]
