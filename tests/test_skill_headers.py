from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
WIKI_TOOL_RUN = re.compile(r"Bash\((?:uv run --with markdown-it-py )?python3 /\S+/learn/wiki/_tools/wiki_html\.py \*\)")


def header(skill: str) -> dict[str, str | list[str]]:
    lines = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"у Скилла {skill} нет шапки"
    keys: dict[str, str | list[str]] = {}
    key = ""
    for line in lines[1 : lines.index("---", 1)]:
        if line.startswith("  - "):
            listed = keys[key]
            assert isinstance(listed, list), f"{key}: список после значения"
            listed.append(line.removeprefix("  - ").strip())
        else:
            key, _, value = line.partition(":")
            keys[key] = value.strip() or []
    return keys


@pytest.mark.parametrize("skill", ["spook-meeting", "spook-lecture"])
def test_skill_header_disables_invocation_by_model(skill):
    assert header(skill)["disable-model-invocation"] == "true"


def test_meeting_skill_allows_work_vault_wiki_reading_web_and_topic_command_and_names_no_model():
    keys = header("spook-meeting")

    assert sorted(keys["allowed-tools"]) == sorted(
        [
            "Read(~/Documents/notes/работа/**)",
            "Edit(~/Documents/notes/работа/**)",
            "Write(~/Documents/notes/работа/**)",
            "Read(~/learn/wiki/**)",
            "WebSearch",
            "WebFetch",
            "Bash(spook topic *)",
        ]
    )
    assert "model" not in keys and "effort" not in keys


def test_lecture_skill_allows_wiki_lectures_and_pages_terms_dictionary_web_wiki_tool_and_topic_command_and_names_no_model():
    keys = header("spook-lecture")
    allowed = keys["allowed-tools"]
    wiki_tool_runs = [tool for tool in allowed if WIKI_TOOL_RUN.fullmatch(tool)]

    assert wiki_tool_runs, allowed
    assert sorted(tool for tool in allowed if tool not in wiki_tool_runs) == sorted(
        [
            "Read(~/learn/wiki/**)",
            "Edit(~/learn/wiki/lectures/**)",
            "Edit(~/learn/wiki/html/**)",
            "Read(~/.claude/skills/wiki/**)",
            "Read(~/Documents/notes/работа/Словарь терминов.md)",
            "WebSearch",
            "WebFetch",
            "Bash(spook topic *)",
        ]
    )
    assert "model" not in keys and "effort" not in keys
