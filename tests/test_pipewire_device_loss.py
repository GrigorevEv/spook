"""Пропажа закреплённого устройства на настоящем входе PipeWire.

Запуск: `uv run pytest -q -m pipewire`. Устройство Системного звука закрепляется в файле настроек за
виртуальным устройством вывода; во время захвата виртуальное устройство удаляется. Захват переходит на
устройство вывода пользователя по умолчанию — только слушает его монитор, умолчаний не меняет и ничего
не воспроизводит. Смена устройства по умолчанию на настоящем входе не проверяется: пришлось бы менять
умолчания пользователя; она проверена на подменах (`test_devices.py`, `test_pipewire_following.py`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_pipewire_capture import (  # noqa: F401 — user_devices_untouched: общая проверка после каждого теста
    OUTPUT_DESCRIPTION,
    nodes,
    user_devices_untouched,
    virtual_node,
    wait_until,
)
from spook.audio import Source
from spook.pipewire import PipeWireAudioInput

pytestmark = pytest.mark.pipewire


@pytest.fixture
def audio_input() -> PipeWireAudioInput:
    """Настоящий вход без закреплений: закрепление приходит из файла настроек."""
    return PipeWireAudioInput()


def child_programs() -> list[str]:
    """Имена программ — прямых потомков процесса теста (захваты и слежение за умолчаниями)."""
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


def test_lost_pinned_virtual_device_leaves_mark_and_warning_and_capture_moves_to_default(spook):
    default_output = PipeWireAudioInput().device(Source.SYSTEM).description

    with virtual_node("Audio/Sink", OUTPUT_DESCRIPTION) as virtual_sink:
        with spook.settings_path.open("a", encoding="utf-8") as file:
            file.write(f'\n[sources]\nsystem = "{virtual_sink}"\n')
        response = spook.start_lecture()
        assert response.status_code == 200, response.text

        with spook.events() as events:
            events.wait_for("state", containing=OUTPUT_DESCRIPTION)
            events.wait_for("levels")

            subprocess.run(["pw-cli", "destroy", str(nodes()[virtual_sink])], check=True, capture_output=True, timeout=10)

            state = events.wait_for("state", containing=default_output)
            mark = events.wait_for("mark", containing="пропало")
            events.wait_for("levels")  # захват продолжается с устройства по умолчанию

    assert "идёт" in state.data
    expected = f"Системный звук: устройство «{OUTPUT_DESCRIPTION}» пропало, захват продолжается с «{default_output}»"
    assert f"<em>0:00:00 {expected}</em>" in mark.data
    assert f'<p class="warning" role="alert">{expected}</p>' in spook.page().text
    # Заметка дописывается по ходу Сессии; после «Стоп» Сессия без Реплик её удалит.
    note = next(spook.lectures_dir.iterdir()).read_text(encoding="utf-8")
    assert f"\n*0:00:00 {expected}*\n" in note

    assert spook.stop().status_code == 200
    with spook.events() as events:
        events.wait_for("state", containing="нет Сессии")
    wait_until(lambda: not {"pw-record", "pw-metadata"} & set(child_programs()), what="остановка процессов захвата")
