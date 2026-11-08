"""Системные уведомления — программой `[programs].notify` (по умолчанию `notify-send`): заголовок и текст.

Уведомляют об итоге Агента, об автостопе Сессии и о перерыве в работе службы. В тестах программа подменяется скриптом-заглушкой,
который запоминает вызовы. Неотправленное уведомление — только запись в журнал службы.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

NOTIFY_TIMEOUT_SECONDS = 10


def send_notification(program: str, title: str, body: str) -> None:
    """Отказ программы (код возврата не 0) — тоже неотправленное уведомление: например,
    `notify-send` без службы уведомлений сеанса."""
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
