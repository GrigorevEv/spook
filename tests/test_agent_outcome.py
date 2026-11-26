from __future__ import annotations

import logging
from pathlib import Path

import pytest

from conftest import note_text
from test_agent_block import ShownJob, jobs_on_screen
from test_agent_launch import journal, run_agent

NOTE_NAME = "2026-11-06 14-02 Встреча"


def running_agent_processes(spook) -> list[Path]:
    control = str(spook.agent.control).encode()
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        if control in arguments and any(argument.endswith(b"codex_stub.py") for argument in arguments):
            found.append(cmdline.parent)
    return found


def test_agent_hanging_longer_than_timeout_is_stopped_and_error_time_exceeded_is_on_screen_and_in_journal(spook):
    spook.write_settings(agent={"timeout_minutes": 0.02})

    run_agent(spook, "hang", until="ошибка")

    assert len(spook.agent.calls()) == 1
    assert running_agent_processes(spook) == []
    shown = jobs_on_screen(spook)[NOTE_NAME]
    assert shown.state == "ошибка" and shown.reason.startswith("Превышено время")
    assert journal(spook).endswith(f"Выжимка: ошибка — {shown.reason}\n")


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("nothing", "Агент завершился, но Выжимки в Заметке нет"),
        ("empty", "Агент завершился, но Выжимки в Заметке нет"),
        ("malformed", "Некорректный ответ Codex: ожидались Выжимка и Тема в JSON"),
    ],
)
def test_unusable_reply_reports_the_reason_and_preserves_the_transcript(spook, scenario, reason):
    run_agent(spook, scenario, until="ошибка")

    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("ошибка", reason)}
    assert "## Суть" not in note_text(spook)
    assert note_text(spook).endswith("**14:02 Собеседники:** Реплика.\n")


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("no-network", "Нет сети"), ("rate-limit", "Исчерпан лимит подписки"),
        ("error-after-summary", "Сбой после ответа"),
    ],
)
def test_failed_turn_reports_reason_in_screen_and_journal_and_leaves_note_intact(spook, scenario, reason):
    run_agent(spook, scenario, until="ошибка")

    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("ошибка", reason)}
    assert journal(spook).endswith(f"Код возврата: 1. Выжимка: ошибка — {reason}\n")
    assert "## Суть" not in note_text(spook)
    assert note_text(spook).endswith("**14:02 Собеседники:** Реплика.\n")


def test_unsent_notification_leaves_outcome_done(spook, caplog):
    caplog.set_level(logging.INFO, logger="spook")
    spook.notify.play("fail")

    run_agent(spook, "summary", until="готово")
    spook.wait_until(lambda: "служба уведомлений недоступна" in caplog.text)

    [warning] = [record for record in caplog.records if record.getMessage().startswith("Уведомление")]
    assert warning.levelname == "WARNING"
    assert "«Выжимка готова»" in warning.getMessage() and "служба уведомлений недоступна" in warning.getMessage()
    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("готово")}
    assert journal(spook).endswith("Код возврата: 0. Выжимка: готово\n")
