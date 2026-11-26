from __future__ import annotations

import json
import logging
import re
import shlex

from conftest import NO_SPEECH, agent_model_and_effort, speech
from test_legacy_lectures import queued_job
from test_notes_screen import write_note

NOTE_NAME = "2026-11-06 14-02 Встреча"


def meeting_with_utterance(spook, *texts: str) -> None:
    spook.recognizer.say(*texts)
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20 * len(texts)))
        events.wait_for("paragraph", containing=texts[-1])


def test_after_stop_agent_runs_once_with_skill_note_model_and_effort_for_meeting(spook):
    spook.write_settings(meeting={"skill": "spook-meeting", "model": "gpt-6.2-sol", "effort": "medium"})
    spook.agent.play("summary")
    meeting_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="готово")

    note_path = spook.meetings_dir / f"{NOTE_NAME}.md"
    [call] = spook.agent.calls()
    assert agent_model_and_effort(call) == ("gpt-6.2-sol", "medium")
    assert call["input"]["skill"] == (spook.skills_dir / "spook-meeting/SKILL.md").read_text(encoding="utf-8")
    assert call["input"]["transcript"].strip() == "**14:02 Собеседники:** Реплика."
    assert "## Суть" in note_path.read_text(encoding="utf-8")


def test_note_with_topic_goes_to_agent_under_its_final_name(spook):
    spook.agent.play("summary")
    meeting_with_utterance(spook, "Реплика.")
    spook.set_topic("Репликация в Raft")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    renamed = spook.meetings_dir / "2026-11-06 14-02 Репликация в Raft.md"
    assert [call["input"]["topic"] for call in spook.agent.calls()] == ["Репликация в Raft"]
    assert "2026-11-06 14-02 Репликация в Raft" in agent.data
    assert "## Суть" in renamed.read_text(encoding="utf-8")
    assert (spook.state_dir / "agent" / "meeting" / "2026-11-06 14-02 Репликация в Raft.log").exists()


def test_agent_that_wrote_summary_above_transcript_gives_done(spook):
    spook.agent.play("summary")
    meeting_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    assert NOTE_NAME in agent.data
    assert (spook.meetings_dir / f"{NOTE_NAME}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:02"\n'
        "tags: [встреча]\n"
        "---\n"
        "\n"
        "## Суть\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Реплика.\n"
    )
    page = spook.page().text
    assert "готово" in page and "ошибка" not in page


def test_agent_that_failed_gives_error_with_its_message(spook):
    spook.agent.play("fail")
    meeting_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="ошибка")

    assert "Сценарий: Агент упал." in agent.data
    assert "Сценарий: Агент упал." in spook.page().text


def test_agent_that_finished_without_summary_gives_error(spook):
    spook.agent.play("nothing")
    meeting_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="ошибка")

    assert "Выжимки в Заметке нет" in agent.data


def test_second_session_waits_in_queue_while_agent_works_on_first_and_start_is_allowed(spook):
    spook.agent.play("wait")
    meeting_with_utterance(spook, "Первая встреча.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="работает")
        spook.clock.advance(60 * 60)

        assert spook.start_meeting().status_code == 200
        spook.recognizer.say("Вторая встреча.")
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Вторая встреча.")
        spook.stop()
        both = events.wait_for("agent", containing="в очереди")
        assert "2026-11-06 15-02 Встреча" in both.data and "работает" in both.data
        spook.wait_until(lambda: len(spook.agent.calls()) == 1)

        saved = json.loads((spook.state_dir / "agent/queue.json").read_text(encoding="utf-8"))
        assert [(job["state"], job["note"]) for job in saved] == [
            ("running", str(spook.meetings_dir / f"{NOTE_NAME}.md")),
            ("queued", str(spook.meetings_dir / "2026-11-06 15-02 Встреча.md")),
        ]

        spook.agent.release()
        first_done = events.wait_for("agent", containing="готово")
        assert "работает" in first_done.data or "в очереди" in first_done.data
        spook.agent.release()
        while "работает" in (final := events.wait_for("agent")).data or "в очереди" in final.data:
            pass

    assert final.data.count("готово") == 2
    assert [call["input"]["transcript"].strip() for call in spook.agent.calls()] == [
        "**14:02 Собеседники:** Первая встреча.",
        "**15:02 Собеседники:** Вторая встреча.",
    ]


def test_agent_log_is_kept_in_state_dir_outside_vault_and_opens_from_page(spook):
    spook.agent.play("fail")
    meeting_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="ошибка")

    log_path = spook.state_dir / "agent" / "meeting" / f"{NOTE_NAME}.log"
    command, output = log_path.read_text(encoding="utf-8").split("\n", 1)
    [call] = spook.agent.calls()
    assert command == "$ " + shlex.join([str(spook.agent.path), *call["argv"]])
    assert output.startswith(f"(рабочий каталог: {call['cwd']})\n")
    assert '"thread.started"' in output
    assert "--- stderr ---\nСценарий: Агент упал." in output
    assert output.endswith("Код возврата: 1. Выжимка: ошибка — Сценарий: Агент упал.\n")
    assert sorted(path.name for path in spook.meetings_dir.iterdir()) == [f"{NOTE_NAME}.md"]

    page = spook.page().text
    link = re.search(r'href="(/agent/log\?note=[^"]+)"', page)
    assert link, page
    journal = spook.http.get(f"{spook.base_url}{link.group(1)}")
    assert journal.status_code == 200
    assert journal.headers["content-type"].startswith("text/plain")
    assert journal.text == log_path.read_text(encoding="utf-8")


def test_notification_reports_ready_summary(spook):
    spook.agent.play("summary")
    meeting_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())

    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", NOTE_NAME]]


def test_notification_reports_agent_error_with_message(spook):
    spook.agent.play("fail")
    meeting_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())

    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Ошибка Агента", f"{NOTE_NAME}: Сценарий: Агент упал."]
    ]


def test_service_journal_records_agent_start_and_outcome(spook, caplog):
    caplog.set_level(logging.INFO, logger="spook")
    spook.write_settings(meeting={"model": "gpt-6.2-sol", "effort": "medium"})
    spook.agent.play("fail")
    meeting_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())
    spook.wait_until(lambda: "Сценарий: Агент упал." in caplog.text)

    journal = [record.getMessage() for record in caplog.records if NOTE_NAME in record.getMessage()]
    assert journal == [
        f"Агент запущен: {NOTE_NAME} — spook-meeting, gpt-6.2-sol, medium",
        f"Агент закончил: {NOTE_NAME} — ошибка: Сценарий: Агент упал.",
    ]


def test_unsent_notification_is_recorded_in_service_journal(spook, caplog):
    spook.agent.play("summary")
    spook.notify.play("fail")
    meeting_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: "Уведомление" in caplog.text)

    [warning] = [record for record in caplog.records if record.getMessage().startswith("Уведомление")]
    assert warning.levelname == "WARNING"
    assert "«Выжимка готова»" in warning.getMessage()
    assert "служба уведомлений недоступна" in warning.getMessage()


def test_session_without_utterances_does_not_run_agent_and_leaves_no_empty_note(spook):
    spook.recognizer.say(NO_SPEECH)
    spook.start_meeting()
    assert (spook.meetings_dir / f"{NOTE_NAME}.md").exists()

    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert list(spook.meetings_dir.iterdir()) == []
    page = spook.page().text
    assert "Выжимка" not in page and "Заметка:" not in page
    assert spook.agent.calls() == [] and spook.notify.calls() == []


def test_agent_queue_survives_service_restart_interrupted_run_is_error_and_waiting_note_runs(spook):
    first = write_note(
        spook.meetings_dir, NOTE_NAME, "2026-11-06", "14:02", "14:35", "встреча", "## Суть\n\nПрежняя Выжимка."
    )
    second = write_note(spook.meetings_dir, "2026-11-06 15-02 Планёрка", "2026-11-06", "15:02", "15:35", "встреча")
    snapshot = first.read_text(encoding="utf-8")
    first.write_text(snapshot.replace("Реплика.", "Изменённый текст."), encoding="utf-8")
    queue = spook.state_dir / "agent" / "queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    jobs = [queued_job(first, "meeting", "running", snapshot), queued_job(second, "meeting", "queued")]
    queue.write_text(json.dumps(jobs), encoding="utf-8")
    spook.agent.play("summary")

    spook.restart_service(60)

    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Ошибка Агента", f"{NOTE_NAME}: Агент прерван перезапуском службы"],
        ["Выжимка готова", second.stem],
    ]
    [call] = spook.agent.calls()
    assert call["input"]["topic"] == "Планёрка"
    assert first.read_text(encoding="utf-8") == snapshot
    assert "Про тесты." in second.read_text(encoding="utf-8")
    page = spook.page().text
    assert "Агент прерван перезапуском службы" in page
    assert "Агент изменил Транскрипт — возвращён исходный." in page
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": NOTE_NAME}).text
    assert journal.endswith(
        "\nОхрана Транскрипта: Агент изменил Транскрипт — возвращён исходный.\n"
        "\nВыжимка: ошибка — Агент прерван перезапуском службы\n"
    )


def test_log_of_unknown_note_is_not_found_and_paths_outside_log_dir_are_refused(spook):
    assert spook.http.get(f"{spook.base_url}/agent/log", params={"note": "нет такой"}).status_code == 404
    spook.state_dir.mkdir(parents=True, exist_ok=True)
    (spook.state_dir / "secret.log").write_text("не журнал", encoding="utf-8")

    response = spook.http.get(f"{spook.base_url}/agent/log", params={"note": "../secret"})

    assert response.status_code == 404
    assert "не журнал" not in response.text
