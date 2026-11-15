from __future__ import annotations

import signal
import threading
from typing import Callable

import uvicorn
from fastapi import FastAPI

from spook.access import AccessGuard

HOST = "127.0.0.1"


class _Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, close_streams: Callable[[], None] | None) -> None:
        super().__init__(config)
        self._close_streams = close_streams

    async def shutdown(self, sockets=None) -> None:
        if self._close_streams is not None:
            self._close_streams()
        await super().shutdown(sockets)


def make_server(app: FastAPI, port: int, log_level: str = "info") -> uvicorn.Server:
    config = uvicorn.Config(
        AccessGuard(app, port=port),
        host=HOST,
        port=port,
        log_level=log_level,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=2,
    )
    return _Server(config, getattr(app.state, "close_event_streams", None))


def exit_quietly_on_stop_signals() -> None:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _exit_quietly)
        signal.signal(signal.SIGINT, _exit_quietly)


def _exit_quietly(signum: int, frame: object) -> None:
    raise SystemExit(0)
