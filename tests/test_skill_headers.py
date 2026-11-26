from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "spook-meeting" / "SKILL.md"


def test_installed_meeting_skill_declares_its_name_and_description_and_uses_model_from_settings():
    opening, header, _ = SKILL.read_text(encoding="utf-8").split("---\n", 2)
    assert opening == ""
    fields = dict(line.split(": ", 1) for line in header.splitlines())
    assert fields["name"] == "spook-meeting"
    assert fields["description"]
    assert "model" not in fields and "effort" not in fields
