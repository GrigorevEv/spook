from __future__ import annotations

import logging
from pathlib import Path

from conftest import speech
from test_agent_block import ShownJob, jobs_on_screen

NOTE_NAME = "2026-11-06 14-02 Встреча"


def run_agent(spook, scenario: str, until: str) -> None:
    spook.agent.play(scenario)
    spook.recognizer.say("Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        events.wait_for("agent", containing=until)


def journal(spook) -> str:
    return (spook.state_dir / "agent" / "meeting" / f"{NOTE_NAME}.log").read_text(encoding="utf-8")


def running_agent_processes(spook) -> list[Path]:
    control = str(spook.agent.control).encode()
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        if control in arguments and any(argument.endswith(b"claude_stub.py") for argument in arguments):
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


def test_agent_that_finished_without_summary_shows_error_with_reason_summary_missing(spook):
    run_agent(spook, "nothing", until="ошибка")

    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("ошибка", "Агент завершился, но Выжимки в Заметке нет")}


def test_note_deleted_by_agent_gives_error_with_reason_summary_missing(spook):
    run_agent(spook, "delete-note", until="ошибка")

    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("ошибка", "Агент завершился, но Выжимки в Заметке нет")}


def test_run_without_network_journal_ends_with_return_code_and_reason_no_network(spook):
    run_agent(spook, "replay no-network 1", until="ошибка")

    assert jobs_on_screen(spook) == {NOTE_NAME: ShownJob("ошибка", "Нет сети")}
    assert journal(spook).endswith("\nКод возврата: 1. Выжимка: ошибка — Нет сети\n")


def test_permission_denials_without_summary_are_reason_of_error_from_its_start(spook):
    run_agent(spook, "replay success-with-denials 0", until="ошибка")

    shown = jobs_on_screen(spook)[NOTE_NAME]
    assert shown.state == "ошибка"
    assert shown.reason.startswith("Отказ в правах: Read ")


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
