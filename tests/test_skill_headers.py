from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


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


def test_meeting_skill_header_disables_invocation_by_model():
    assert header("spook-meeting")["disable-model-invocation"] == "true"


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
