from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

NOTIFY_TIMEOUT_SECONDS = 10


def send_notification(program: str, title: str, body: str) -> None:
    try:
        completed = subprocess.run(
            [program, title, body],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=NOTIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("Уведомление «%s» не отправлено через %s: %s", title, program, error)
        return
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        log.warning(
            "Уведомление «%s» не отправлено: %s завершился с кодом %d: %s", title, program, completed.returncode, stderr
        )
