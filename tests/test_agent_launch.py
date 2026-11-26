from __future__ import annotations

import json
from pathlib import Path

from conftest import agent_model_and_effort, speech

NOTE_NAME = "2026-11-06 14-02 Встреча"


def run_agent(spook, scenario: str = "summary", until: str = "готово"):
    spook.agent.play(scenario)
    spook.recognizer.say("Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        return events.wait_for("agent", containing=until)


def journal(spook) -> str:
    return (spook.state_dir / "agent" / "meeting" / f"{NOTE_NAME}.log").read_text(encoding="utf-8")


def test_codex_receives_data_through_stdin_and_runs_in_a_temporary_folder(spook):
    run_agent(spook)

    [call] = spook.agent.calls()
    args = call["argv"]
    assert "exec" in args and args[-1] == "-"
    assert "--json" in args and "--ephemeral" in args and "--no-daemon" in args
    assert agent_model_and_effort(call) == ("gpt-6.2-astra", "high")
    assert set(call["input"]) == {"skill", "date", "topic", "transcript", "terms_dictionary", "vault_notes", "wiki_notes"}
    schema = call["schema"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) == {"summary", "topic"}
    assert schema["properties"] == {"summary": {"type": "string"}, "topic": {"type": "string"}}
    assert call["input"]["transcript"].strip() == "**14:02 Собеседники:** Реплика."
    assert "Реплика." not in " ".join(args)
    assert str(spook.meetings_dir) not in " ".join(args)
    cwd = Path(call["cwd"])
    assert not cwd.is_relative_to(spook.meetings_dir.parent)
    assert not cwd.exists()


def test_codex_uses_isolated_permissions_and_cached_search(spook):
    run_agent(spook)

    [call] = spook.agent.calls()
    args = call["argv"]
    assert {"--ignore-user-config", "--ignore-rules"} <= set(args)
    settings = {args[i + 1] for i, arg in enumerate(args) if arg == "-c"}
    assert {
        'approval_policy="never"', 'permissions.spook.filesystem={":root"="none"}',
        "permissions.spook.network.enabled=false", 'web_search="cached"', "mcp_servers={}",
        'shell_environment_policy.inherit="none"',
    } <= settings
    disabled = {args[i + 1] for i, arg in enumerate(args) if arg == "--disable"}
    assert {"apps", "plugins", "hooks", "shell_tool", "unified_exec", "browser_use", "computer_use", "multi_agent"} <= disabled


def test_selected_skill_is_sent_in_stdin_and_model_and_effort_come_from_settings(spook):
    skill = spook.skills_dir / "my-meeting" / "SKILL.md"
    skill.parent.mkdir()
    text = "---\nname: my-meeting\ndescription: Выжимка.\n---\n\nСобери задачи и сроки Встречи."
    skill.write_text(text, encoding="utf-8")
    spook.write_settings(meeting={"skill": "my-meeting", "model": "gpt-6.2-sol", "effort": "medium"})

    run_agent(spook)

    [call] = spook.agent.calls()
    assert call["input"]["skill"] == text
    assert agent_model_and_effort(call) == ("gpt-6.2-sol", "medium")


def test_missing_skill_keeps_note_and_reports_error_before_starting_codex(spook):
    spook.write_settings(meeting={"skill": "absent"})

    result = run_agent(spook, until="ошибка")

    assert "Не удалось прочитать Скилл absent" in result.data
    assert spook.agent.calls() == []
    note = spook.meetings_dir / f"{NOTE_NAME}.md"
    assert "## Суть" not in note.read_text(encoding="utf-8")
    assert note.read_text(encoding="utf-8").endswith("**14:02 Собеседники:** Реплика.\n")


def test_journal_keeps_codex_events_and_stderr(spook):
    run_agent(spook, "fail", until="ошибка")

    text = journal(spook)
    events = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert [event["type"] for event in events][:2] == ["thread.started", "turn.started"]
    assert "--- stderr ---\nСценарий: Агент упал." in text
    assert text.endswith("Код возврата: 1. Выжимка: ошибка — Сценарий: Агент упал.\n")


def test_launch_can_be_wrapped_by_command_from_settings_with_timeout_in_seconds(spook):
    spook.write_settings(agent={"timeout_minutes": 2, "wrapper": "env SPOOK_UNIT_LIMIT={timeout}"})

    run_agent(spook)

    assert journal(spook).startswith(f"$ env SPOOK_UNIT_LIMIT=120 {spook.agent.path} ")
    assert len(spook.agent.calls()) == 1
