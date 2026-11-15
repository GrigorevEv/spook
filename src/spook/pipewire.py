from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable, Mapping

import numpy as np

from spook.audio import SAMPLE_RATE, AudioSink, CaptureUnavailable, Device, DeviceChange, DeviceSink, Source

log = logging.getLogger(__name__)

DEFAULT_KEY = {Source.SYSTEM: "default.audio.sink", Source.MIC: "default.audio.source"}
MEDIA_CLASS = {Source.SYSTEM: ("Audio/Sink",), Source.MIC: ("Audio/Source", "Audio/Source/Virtual")}
MISSING_DEFAULT = {Source.SYSTEM: "нет устройства вывода по умолчанию", Source.MIC: "нет устройства ввода по умолчанию"}

_S16_MONO_FRAME_BYTES = 2
CHUNK_SECONDS = 0.1
CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_SECONDS) * _S16_MONO_FRAME_BYTES
FAST_FAILURE_WINDOW = 3.0
STOP_TIMEOUT = 3.0
RETRY_SECONDS = 0.25
DEFAULT_SETTLE_SECONDS = 2.0

_METADATA_UPDATE = re.compile(r"^update: id:0 key:'(?P<key>[^']*)' value:'(?P<value>.*)' type:'[^']*'$")

_CAPTURE_SINK_MONITOR = "stream.capture.sink=true"
_FORBID_WIREPLUMBER_RELINK = "node.dont-reconnect=true"


class PipeWireAudioInput:
    def __init__(self, targets: Mapping[Source, str] | None = None) -> None:
        self._targets = {source: name for source, name in (targets or {}).items() if name}

    def start(self, source: Source, sink: AudioSink, on_device_change: DeviceSink, pinned: str = "") -> PipeWireCapture:
        pinned = pinned or self._targets.get(source, "")
        device = self.device(source, pinned)
        capture = PipeWireCapture(source, device, pinned, sink, on_device_change)
        capture.begin()
        log.info("Захват %s начат: %s", source.label, device.description)
        return capture

    def devices(self, source: Source) -> list[Device]:
        return sorted(_devices(pw_dump(), source).values(), key=lambda device: (device.description, device.name))

    def device(self, source: Source, pinned: str = "") -> Device:
        objects = pw_dump()
        devices = _devices(objects, source)
        target = pinned or self._targets.get(source)
        if target:
            if target not in devices:
                raise CaptureUnavailable(
                    f"Нет устройства для захвата: {source.label} — закреплённое устройство «{target}» не найдено"
                )
            return devices[target]
        name = _default_name(objects, DEFAULT_KEY[source])
        if not name or name not in devices:
            raise CaptureUnavailable(f"Нет устройства для захвата: {source.label} — {MISSING_DEFAULT[source]}")
        return devices[name]


def record_command(source: Source, device: Device) -> list[str]:
    properties = [f"node.name=spook-{source.value}", _FORBID_WIREPLUMBER_RELINK]
    if source is Source.SYSTEM:
        properties.insert(0, _CAPTURE_SINK_MONITOR)
    return [
        "pw-record",
        "--target", device.name,
        "-P", "{ " + " ".join(properties) + " }",
        "--rate", str(SAMPLE_RATE),
        "--channels", "1",
        "--format", "s16",
        "-",
    ]


class PipeWireCapture:
    def __init__(
        self, source: Source, device: Device, pinned: str, sink: AudioSink, on_device_change: DeviceSink
    ) -> None:
        self._source = source
        self._device = device
        self._pinned = pinned
        self._sink = sink
        self._on_device_change = on_device_change
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._recorder: _Recorder | None = None
        self._watcher: subprocess.Popen | None = None

    @property
    def device_name(self) -> str:
        return self._device.description

    def begin(self) -> None:
        recorder = _Recorder(self._source, self._device, self._sink, self._on_recorder_end)
        recorder.wait_until_streaming()
        with self._lock:
            self._recorder = recorder
        self._replay_end_missed_while_starting(recorder)
        self._watch_defaults()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            recorder, self._recorder = self._recorder, None
            watcher, self._watcher = self._watcher, None
        if watcher is not None and watcher.poll() is None:
            watcher.terminate()
            try:
                watcher.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.wait()
        if recorder is not None:
            recorder.stop()

    def _watch_defaults(self) -> None:
        try:
            watcher = subprocess.Popen(
                ["pw-metadata", "-m", "0"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
            )
        except OSError as error:
            log.warning("Смена устройства по умолчанию не отслеживается (%s): %s", self._source.label, error)
            return
        with self._lock:
            if self._stopping.is_set():
                watcher.terminate()
                watcher.wait()
                return
            self._watcher = watcher
        threading.Thread(
            target=self._read_defaults, args=(watcher,), name=f"spook-defaults-{self._source.value}", daemon=True
        ).start()

    def _read_defaults(self, watcher: subprocess.Popen) -> None:
        assert watcher.stdout is not None
        for line in watcher.stdout:
            name = _default_update(line.strip(), DEFAULT_KEY[self._source])
            if name:
                self._follow_default(name)

    def _follow_default(self, name: str) -> None:
        with self._lock:
            if self._stopping.is_set() or self._pinned or self._recorder is None or name == self._device.name:
                return
            current, self._recorder = self._recorder, None
        current.stop()
        self._reacquire()

    def _on_recorder_end(self, recorder: _Recorder) -> None:
        with self._lock:
            if self._stopping.is_set() or self._recorder is not recorder:
                return
            self._recorder = None
        threading.Thread(target=self._reacquire, name=f"spook-reacquire-{self._source.value}", daemon=True).start()

    def _reacquire(self) -> None:
        previous = self._device
        lost: bool | None = None
        absent_reported = False
        settle_deadline = time.monotonic() + DEFAULT_SETTLE_SECONDS
        while not self._stopping.is_set():
            try:
                objects = pw_dump()
            except CaptureUnavailable as error:
                log.warning("Захват %s ждёт PipeWire: %s", self._source.label, error)
                self._stopping.wait(RETRY_SECONDS)
                continue
            devices = _devices(objects, self._source)
            if lost is None:
                lost = previous.name not in devices
                if lost:
                    log.warning("Устройство захвата %s пропало: %s", self._source.label, previous.description)
                    self._pinned = ""
            target = devices.get(self._pinned or _default_name(objects, DEFAULT_KEY[self._source]) or "")
            if target is None:
                if not absent_reported and time.monotonic() >= settle_deadline:
                    absent_reported = True
                    log.warning("Нет устройства для захвата: %s", self._source.label)
                    self._report(DeviceChange(self._source, "", lost=lost))
                self._stopping.wait(RETRY_SECONDS)
                continue
            if target.name == previous.name:
                self._stopping.wait(RETRY_SECONDS)
            try:
                recorder = _Recorder(self._source, target, self._sink, self._on_recorder_end)
                recorder.wait_until_streaming()
            except CaptureUnavailable as error:
                log.warning("%s", error)
                self._stopping.wait(RETRY_SECONDS)
                continue
            with self._lock:
                abandoned = self._stopping.is_set()
                if not abandoned:
                    self._recorder = recorder
                    self._device = target
            if abandoned:
                recorder.stop()
                return
            log.info("Захват %s перешёл на %s", self._source.label, target.description)
            if lost and not absent_reported:
                self._report(DeviceChange(self._source, target.description, lost=True))
            elif target.name != previous.name or absent_reported:
                self._report(DeviceChange(self._source, target.description))
            self._replay_end_missed_while_starting(recorder)
            return

    def _replay_end_missed_while_starting(self, recorder: _Recorder) -> None:
        if recorder.finished:
            self._on_recorder_end(recorder)

    def _report(self, change: DeviceChange) -> None:
        if not self._stopping.is_set():
            self._on_device_change(change)


class _Recorder:
    def __init__(
        self, source: Source, device: Device, sink: AudioSink, on_end: Callable[[_Recorder], None]
    ) -> None:
        self._source = source
        self._device = device
        self._sink = sink
        self._on_end = on_end
        try:
            self._process = subprocess.Popen(
                record_command(source, device),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise CaptureUnavailable(f"Не удалось начать захват ({source.label}): {error}") from error
        self._stopping = False
        self._streaming = threading.Event()
        self._finished = threading.Event()
        self._stderr_tail: deque[str] = deque(maxlen=5)
        threading.Thread(target=self._drain_stderr, name=f"spook-capture-{source.value}-stderr", daemon=True).start()
        self._reader = threading.Thread(target=self._pump, name=f"spook-capture-{source.value}", daemon=True)
        self._reader.start()

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    def wait_until_streaming(self) -> None:
        deadline = time.monotonic() + FAST_FAILURE_WINDOW
        while time.monotonic() < deadline:
            if self._streaming.is_set():
                return
            if self._finished.is_set():
                raise CaptureUnavailable(
                    f"Не удалось начать захват ({self._source.label}, {self._device.description}): "
                    f"{self._failure_reason()}"
                )
            time.sleep(0.02)

    def stop(self) -> None:
        self._stopping = True
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=STOP_TIMEOUT)

    def _pump(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        try:
            while True:
                chunk = stdout.read(CHUNK_BYTES)
                if len(chunk) < CHUNK_BYTES:
                    break
                self._streaming.set()
                if self._stopping:
                    continue
                self._sink(np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0)
        finally:
            self._process.wait()
            self._finished.set()
            if not self._stopping:
                log.warning(
                    "Захват %s (%s) прервался: %s", self._source.label, self._device.description, self._failure_reason()
                )
                self._on_end(self)

    def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        assert stderr is not None
        for line in stderr:
            text = line.decode(errors="replace").strip()
            if text:
                self._stderr_tail.append(text)

    def _failure_reason(self) -> str:
        reason = "; ".join(self._stderr_tail)
        return reason or f"pw-record завершился с кодом {self._process.returncode}"


def pw_dump() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(["pw-dump"], capture_output=True, check=True, timeout=10)
        objects = json.loads(completed.stdout)
    except FileNotFoundError as error:
        raise CaptureUnavailable("PipeWire недоступен: не найдена программа pw-dump") from error
    except subprocess.CalledProcessError as error:
        reason = error.stderr.decode(errors="replace").strip() or f"код {error.returncode}"
        raise CaptureUnavailable(f"PipeWire недоступен: {reason}") from error
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise CaptureUnavailable(f"PipeWire недоступен: {error}") from error
    if not isinstance(objects, list):
        raise CaptureUnavailable("PipeWire недоступен: неожиданный ответ pw-dump")
    return objects


def _node_props(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        (obj.get("info") or {}).get("props") or {}
        for obj in objects
        if obj.get("type") == "PipeWire:Interface:Node"
    ]


def _default_name(objects: list[dict[str, Any]], key: str) -> str | None:
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Metadata" or (obj.get("props") or {}).get("metadata.name") != "default":
            continue
        for entry in obj.get("metadata") or []:
            if entry.get("key") == key:
                value = entry.get("value")
                if isinstance(value, dict):
                    return value.get("name")
                if isinstance(value, str):
                    try:
                        return json.loads(value).get("name")
                    except (json.JSONDecodeError, AttributeError):
                        return None
    return None


def _devices(objects: list[dict[str, Any]], source: Source) -> dict[str, Device]:
    return {
        props["node.name"]: Device(props["node.name"], props.get("node.description") or props["node.name"])
        for props in _node_props(objects)
        if props.get("media.class") in MEDIA_CLASS[source] and props.get("node.name")
    }


def _default_update(line: str, key: str) -> str | None:
    match = _METADATA_UPDATE.match(line)
    if match is None or match["key"] != key:
        return None
    try:
        value = json.loads(match["value"])
    except json.JSONDecodeError:
        return None
    name = value.get("name") if isinstance(value, dict) else None
    return name if isinstance(name, str) and name else None
