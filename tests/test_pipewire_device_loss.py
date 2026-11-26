from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_pipewire_capture import (  # noqa: F401
    OUTPUT_DESCRIPTION,
    node_ids_by_name,
    pw_dump,
    user_devices_untouched,
    virtual_node,
    virtual_source,
    wait_until,
)
from spook.audio import Source
from spook.pipewire import PipeWireAudioInput

pytestmark = pytest.mark.pipewire


@pytest.fixture
def audio_input(virtual_source: str) -> PipeWireAudioInput:
    return PipeWireAudioInput(targets={Source.MIC: virtual_source})


@pytest.fixture
def virtual_default_output(monkeypatch: pytest.MonkeyPatch):
    description = "Spook test fallback output"
    with virtual_node("Audio/Sink", description) as name:
        def dump_with_virtual_default():
            objects = pw_dump()
            for obj in objects:
                if (obj.get("props") or {}).get("metadata.name") == "default":
                    for entry in obj.get("metadata") or []:
                        if entry.get("key") == "default.audio.sink":
                            entry["value"] = {"name": name}
            return objects

        monkeypatch.setattr("spook.pipewire.pw_dump", dump_with_virtual_default)
        yield description


def child_programs() -> list[str]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        name, _, rest = stat.rpartition(")")
        if int(rest.split()[1]) == os.getpid():
            found.append(name.partition("(")[2])
    return found


def test_lost_pinned_virtual_device_leaves_mark_and_warning_and_capture_moves_to_default(spook, virtual_default_output):
    with virtual_node("Audio/Sink", OUTPUT_DESCRIPTION) as virtual_sink:
        with spook.settings_path.open("a", encoding="utf-8") as file:
            file.write(f'\n[sources]\nsystem = "{virtual_sink}"\n')
        response = spook.start_meeting()
        assert response.status_code == 200, response.text

        with spook.events() as events:
            events.wait_for("state", containing=OUTPUT_DESCRIPTION)
            events.wait_for("levels")

            subprocess.run(["pw-cli", "destroy", str(node_ids_by_name()[virtual_sink])], check=True, capture_output=True, timeout=10)

            state = events.wait_for("state", containing=virtual_default_output)
            mark = events.wait_for("mark", containing="пропало")
            events.wait_for("levels")

    assert "идёт" in state.data
    expected = f"Системный звук: устройство «{OUTPUT_DESCRIPTION}» пропало, захват продолжается с «{virtual_default_output}»"
    assert f"<em>14:02 {expected}</em>" in mark.data
    assert f'<p class="warning" role="alert">{expected}</p>' in spook.page().text
    note = next(spook.meetings_dir.iterdir()).read_text(encoding="utf-8")
    assert f"\n*14:02 {expected}*\n" in note

    assert spook.stop().status_code == 200
    with spook.events() as events:
        events.wait_for("state", containing="нет Сессии")
    wait_until(lambda: not {"pw-record", "pw-metadata"} & set(child_programs()), what="остановка процессов захвата")
