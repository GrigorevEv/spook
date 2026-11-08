"""Настоящий захват из PipeWire на виртуальных устройствах, созданных на время теста.

Запуск: `uv run pytest -q -m pipewire`. Устройства пользователя по умолчанию не меняются; звук
проигрывается только в виртуальное устройство (`pw-play` с `node.dont-reconnect=true` при пропаже цели
завершается ошибкой, а не переключается на наушники пользователя); виртуальные узлы удаляются после теста.

Пропажа устройства проверяется удалением виртуального устройства, закреплённого в настройках, во время
захвата. Смена устройства по умолчанию здесь не проверяется: для этого пришлось бы менять умолчания
пользователя — этот сценарий покрыт только на подменах (`test_devices.py`, `test_pipewire_following.py`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import pytest

from spook.audio import Source
from spook.pipewire import PipeWireAudioInput

from test_settings_screen import settings_form

pytestmark = pytest.mark.pipewire

SAMPLE_RATE = 16_000
TONE_AMPLITUDE = 0.1  # −20 дБ полной шкалы: RMS синуса = 0.1/√2 ≈ 0.0707, пик = 0.1
TONE_RMS = TONE_AMPLITUDE / np.sqrt(2)
OUTPUT_DESCRIPTION = "Spook test output"
MICROPHONE_DESCRIPTION = "Spook test microphone"


# --- обвязка PipeWire -------------------------------------------------------------


def pw_dump() -> list[dict[str, Any]]:
    return json.loads(subprocess.run(["pw-dump"], capture_output=True, check=True, timeout=10).stdout)


def nodes() -> dict[str, int]:
    """`node.name` → id всех узлов."""
    return {
        obj["info"]["props"]["node.name"]: obj["id"]
        for obj in pw_dump()
        if obj.get("type") == "PipeWire:Interface:Node" and "node.name" in (obj.get("info") or {}).get("props", {})
    }


def user_defaults() -> dict[str, Any]:
    """Устройства по умолчанию глазами независимой программы `pw-metadata`."""
    return {
        key: subprocess.run(["pw-metadata", "0", key], capture_output=True, text=True, check=True).stdout.strip()
        for key in ("default.audio.sink", "default.audio.source", "default.configured.audio.sink", "default.configured.audio.source")
    }


def wait_until(condition: Callable[[], bool], timeout: float = 5.0, what: str = "условие") -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"{what} не наступило за {timeout} с"
        time.sleep(0.02)


@contextmanager
def virtual_node(media_class: str, description: str) -> Iterator[str]:
    """Виртуальное устройство на время теста; `priority.session=0` — чтобы не стать умолчанием."""
    name = f"spook-test-{uuid.uuid4().hex[:8]}"
    properties = (
        f"{{ factory.name=support.null-audio-sink node.name={name} node.description=\"{description}\" "
        f"media.class={media_class} object.linger=true audio.position=[FL,FR] monitor.channel-volumes=true "
        "priority.session=0 }"
    )
    subprocess.run(["pw-cli", "create-node", "adapter", properties], check=True, capture_output=True, timeout=10)
    try:
        wait_until(lambda: name in nodes(), what="появление виртуального устройства")
        yield name
    finally:
        node_id = nodes().get(name)
        if node_id is not None:
            subprocess.run(["pw-cli", "destroy", str(node_id)], check=True, capture_output=True, timeout=10)
        wait_until(lambda: name not in nodes(), what="удаление виртуального устройства")


@pytest.fixture
def virtual_sink() -> Iterator[str]:
    with virtual_node("Audio/Sink", OUTPUT_DESCRIPTION) as name:
        yield name


@pytest.fixture
def virtual_source() -> Iterator[str]:
    with virtual_node("Audio/Source/Virtual", MICROPHONE_DESCRIPTION) as name:
        yield name


@pytest.fixture(autouse=True)
def user_devices_untouched() -> Iterator[None]:
    before = user_defaults()
    yield
    assert user_defaults() == before, "изменились устройства пользователя по умолчанию"
    assert not [name for name in nodes() if name.startswith("spook-test-")], "остались виртуальные узлы теста"


def tone(seconds: float) -> bytes:
    moments = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 440.0 * moments) * TONE_AMPLITUDE * 32767).astype("<i2").tobytes()


def play_tone(target: str, seconds: float, into_ports: tuple[str, ...] = ()) -> subprocess.Popen:
    """Проигрывает синус только в узел `target` (по stdin, без файлов).

    Без `into_ports` цель задаётся `--target`; виртуальный источник целью воспроизведения быть не может,
    поэтому для него поток создаётся несвязанным и линкуется в его входы вручную.
    """
    player_name = f"spook-test-player-{uuid.uuid4().hex[:8]}"
    if into_ports:
        properties = f"{{ node.name={player_name} node.autoconnect=false node.dont-reconnect=true }}"
        target_argument = "0"
    else:
        properties = "{ node.dont-reconnect=true }"
        target_argument = target
    player = subprocess.Popen(
        ["pw-play", "--target", target_argument, "-P", properties,
         "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "-"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if into_ports:
        wait_until(lambda: player_name in nodes(), what="появление потока воспроизведения")
        for port in into_ports:
            subprocess.run(["pw-link", f"{player_name}:output_MONO", f"{target}:{port}"], check=True, timeout=10)

    def feed() -> None:
        try:
            assert player.stdin is not None
            player.stdin.write(tone(seconds))
            player.stdin.close()
        except BrokenPipeError:
            pass

    threading.Thread(target=feed, daemon=True).start()
    return player


# --- слежение за диском и журналом ----------------------------------------------------


class DiskWatch:
    """Снимок временных каталогов: за время захвата на диске не должно появиться ни одного файла."""

    def __init__(self, *roots: Path) -> None:
        self._roots = roots
        self._before = self._snapshot()

    def _snapshot(self) -> set[Path]:
        seen: set[Path] = set()
        for root in self._roots:
            if root.exists():
                seen.update(path for path in root.iterdir() if not path.is_dir())
        return seen

    def new_files(self) -> set[Path]:
        return self._snapshot() - self._before


def capture_processes(target: str) -> list[int]:
    """Процессы захвата, нацеленные на наше виртуальное устройство."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if arguments and Path(arguments[0].decode(errors="replace")).name == "pw-record" and target.encode() in arguments:
            found.append(int(entry.name))
    return found


def files_open_for_writing(pid: int) -> list[str]:
    """Обычные файлы, открытые процессом на запись (каналы, сокеты, память и /dev не считаются)."""
    writable = []
    for fd in (Path("/proc") / str(pid) / "fd").iterdir():
        try:
            path = os.readlink(fd)
            flags = int(next(line for line in (fd.parent.parent / "fdinfo" / fd.name).read_text().splitlines() if line.startswith("flags:")).split()[1], 8)
        except (OSError, StopIteration):
            continue
        is_regular = path.startswith("/") and not path.startswith(("/dev/", "/memfd:"))
        if is_regular and flags & (os.O_WRONLY | os.O_RDWR):
            writable.append(path)
    return writable


# --- тесты -------------------------------------------------------------------------------


@pytest.fixture
def audio_input(virtual_sink: str) -> PipeWireAudioInput:
    """Настоящий вход, у которого Системный звук закреплён за виртуальным устройством вывода."""
    return PipeWireAudioInput(targets={Source.SYSTEM: virtual_sink})


def test_lecture_on_real_input_shows_output_device_and_streams_its_levels(spook, virtual_sink, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="spook")
    disk = DiskWatch(tmp_path, Path(tempfile.gettempdir()), Path("/var/tmp"), Path("/dev/shm"), Path.cwd())

    response = spook.start_lecture()
    assert response.status_code == 200, response.text

    with spook.events() as events:
        state = events.next_event()
        assert state.name == "state" and OUTPUT_DESCRIPTION in state.data

        quiet = json.loads(events.wait_for("levels").data)["system"]
        assert quiet["rms"] < 0.001, "монитор виртуального устройства без воспроизведения должен быть тишиной"

        player = play_tone(virtual_sink, seconds=2.0)
        loud: list[dict[str, float]] = []
        deadline = time.monotonic() + 5
        while len(loud) < 5 and time.monotonic() < deadline:
            level = json.loads(events.wait_for("levels").data)["system"]
            if level["rms"] > 0.02:
                loud.append(level)

        processes = capture_processes(virtual_sink)
        assert processes, "процесс захвата не найден"
        assert all(files_open_for_writing(pid) == [] for pid in processes)
        player.wait(timeout=10)

    assert len(loud) >= 5, "уровень не поднялся над тишиной: монитор устройства не захвачен"
    assert max(level["rms"] for level in loud) == pytest.approx(TONE_RMS, abs=0.005)
    assert max(level["peak"] for level in loud) == pytest.approx(TONE_AMPLITUDE, abs=0.01)

    assert spook.stop().status_code == 200
    with spook.events() as events:
        events.wait_for("state", containing="нет Сессии")

    assert disk.new_files() == set()
    journal = [record.getMessage() for record in caplog.records if record.name.startswith("spook")]
    assert journal, "служба ничего не записала в журнал о захвате"
    looks_like_audio = re.compile(r"b'|b\"|\[\s*-?\d")  # байты или массив отсчётов, пусть и сокращённый numpy
    assert not [message for message in journal if looks_like_audio.search(message)], "в журнал попал звук"


def test_microphone_is_captured_from_input_device(virtual_source):
    chunks: list[np.ndarray] = []
    changes: list[object] = []
    capture = PipeWireAudioInput().start(Source.MIC, chunks.append, changes.append, pinned=virtual_source)
    try:
        wait_until(lambda: len(chunks) >= 1, what="первая порция звука")
        player = play_tone(virtual_source, seconds=2.0, into_ports=("input_FL", "input_FR"))
        wait_until(lambda: sum(float(np.sqrt(np.mean(chunk**2))) > 0.02 for chunk in chunks) >= 5, what="звук в Микрофоне")
        player.wait(timeout=10)
    finally:
        capture.stop()

    assert capture.device_name == MICROPHONE_DESCRIPTION
    assert changes == [], "закреплённый Микрофон никуда не переходил"
    assert max(float(np.sqrt(np.mean(chunk**2))) for chunk in chunks) == pytest.approx(TONE_RMS, abs=0.005)


def test_without_pinning_sources_use_pipewire_default_devices():
    audio = PipeWireAudioInput()
    defaults = user_defaults()

    assert audio.device(Source.SYSTEM).name in defaults["default.audio.sink"]
    assert audio.device(Source.MIC).name in defaults["default.audio.source"]


def test_settings_screen_lists_virtual_devices_of_each_source_for_pinning(spook, virtual_sink, virtual_source):
    options = settings_form(spook).options

    assert options["sources.system"][0] == ("", "по умолчанию")
    assert (virtual_sink, OUTPUT_DESCRIPTION) in options["sources.system"]
    assert (virtual_source, MICROPHONE_DESCRIPTION) in options["sources.mic"]
    assert virtual_source not in dict(options["sources.system"])
    assert virtual_sink not in dict(options["sources.mic"])
