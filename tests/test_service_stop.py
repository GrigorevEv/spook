"""Остановка службы: `systemctl --user stop` шлёт `spook serve` SIGTERM. Открытые вкладки (потоки
событий) не должны задерживать остановку и оставлять в журнале трассировку."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import EventStream, free_port


def start_serve(tmp_path: Path, port: int) -> subprocess.Popen[str]:
    settings = tmp_path / "settings.toml"
    settings.write_text(
        f'port = {port}\n[recognition]\nmodel_dir = "{tmp_path / "model"}"\n', encoding="utf-8"
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "spook.cli", "serve", "--settings", str(settings)],
        env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 15
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return process
        except OSError:
            assert process.poll() is None, process.communicate()[0]
            assert time.monotonic() < deadline, "служба не поднялась"
            time.sleep(0.05)


def test_service_with_open_tabs_stops_at_once_without_traceback(tmp_path):
    port = free_port()
    process = start_serve(tmp_path, port)
    try:
        with httpx.Client(timeout=5.0) as http:
            with EventStream(http, f"http://127.0.0.1:{port}/events", 15.0) as first_tab, EventStream(
                http, f"http://127.0.0.1:{port}/events", 15.0
            ) as second_tab:
                assert first_tab.next_event().name == "state"
                assert second_tab.next_event().name == "state"

                stopped_at = time.monotonic()
                process.send_signal(signal.SIGTERM)
                code = process.wait(timeout=10)
                elapsed = time.monotonic() - stopped_at
    finally:
        if process.poll() is None:
            process.kill()
    output = process.communicate()[0]

    assert code == 0, output
    assert elapsed < 1.5, f"остановка заняла {elapsed:.1f} с\n{output}"
    assert "Traceback" not in output and "CancelledError" not in output, output
