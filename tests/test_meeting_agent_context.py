from __future__ import annotations

import json
from pathlib import Path

from conftest import speech

PRIVATE_TEXT = "Внутренний номер обращения 9418."


def finish_meeting(spook) -> None:
    spook.agent.play("summary")
    spook.recognizer.say("Обсудим релиз.")
    assert spook.start_meeting().status_code == 200
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Обсудим релиз.")
        spook.stop()
        events.wait_for("agent", containing="готово")


def wisp_article(path: Path, title: str, section: str) -> None:
    meta = json.dumps({"title": title, "section": section}, ensure_ascii=False)
    path.write_text(
        f'<script type="application/json" id="wiki-meta">{meta}</script><p>{PRIVATE_TEXT}</p>',
        encoding="utf-8",
    )


def test_meeting_agent_gets_only_titles_and_relative_files_from_wisp_index(spook):
    wisp_article(spook.meetings_dir / "raft.html", "Raft", "Системы")
    wisp_article(spook.meetings_dir / "plan-reliza.html", "План релиза", "Встречи")
    (spook.meetings_dir / "unpublished.html").write_text(f"<p>{PRIVATE_TEXT}</p>", encoding="utf-8")
    (spook.meetings_dir / "Старая заметка.md").write_text(PRIVATE_TEXT, encoding="utf-8")

    finish_meeting(spook)

    [call] = spook.agent.calls()
    context = call["input"]
    assert sorted(context["wisp_notes"], key=lambda entry: entry["file"]) == [
        {"title": "План релиза", "file": "plan-reliza.html"},
        {"title": "Raft", "file": "raft.html"},
    ]
    assert context["terms_dictionary"] == spook.settings.terms_dictionary.read_text(encoding="utf-8")
    assert context["transcript"] == "**14:02 Собеседники:** Обсудим релиз."
    assert context["date"] == "2026-11-06"
    assert PRIVATE_TEXT not in str(context) and str(spook.wisp_dir) not in str(context)


def test_meeting_agent_creates_terms_dictionary_template_when_file_is_missing(spook):
    dictionary = spook.settings.terms_dictionary
    assert not dictionary.exists()

    finish_meeting(spook)

    template = dictionary.read_text(encoding="utf-8")
    assert template.startswith("# Словарь терминов\n")
    assert "Агент" in template


def test_meeting_agent_leaves_existing_terms_dictionary_untouched(spook):
    dictionary = spook.settings.terms_dictionary
    dictionary.parent.mkdir(parents=True, exist_ok=True)
    dictionary.write_text("- Kubernetes — кубер\n", encoding="utf-8")

    finish_meeting(spook)

    assert dictionary.read_text(encoding="utf-8") == "- Kubernetes — кубер\n"
