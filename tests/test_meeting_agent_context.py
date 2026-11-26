from __future__ import annotations

from pathlib import Path

from conftest import speech

SECRET = "пароль: hunter2"


def finished_meeting(spook) -> Path:
    spook.agent.play("summary")
    spook.recognizer.say("Обсудим релиз.")
    assert spook.start_meeting().status_code == 200
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Обсудим релиз.")
        spook.stop()
        events.wait_for("agent", containing="готово")
    return spook.meetings_dir / "2026-11-06 14-02 Встреча.md"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_meeting_agent_gets_names_of_work_vault_and_wiki_notes_without_their_content(spook):
    vault = spook.meetings_dir.parent
    write(spook.meetings_dir / "2026-10-01 10-00 Планирование релиза.md", f"{SECRET}\n")
    write(vault / "личное" / "Пароли.md", f"{SECRET}\n")
    write(vault / ".obsidian" / "Служебное.md", "служебное\n")
    write(vault / "работа" / "схема.png", "не заметка\n")
    wiki = spook.wiki_dir
    write(wiki / "Raft.md", f"{SECRET}\n")
    write(wiki / "SCST" / "SCST.md", f"{SECRET}\n")

    note = finished_meeting(spook)

    [call] = spook.agent.calls()
    context = call["input"]
    assert set(context["vault_notes"]) == {"2026-10-01 10-00 Планирование релиза", "Пароли", "Словарь терминов"}
    assert context["wiki_notes"] == [
        "[Raft](obsidian://open?vault=wiki&file=Raft)",
        "[SCST](obsidian://open?vault=wiki&file=SCST%2FSCST)",
    ]
    assert context["terms_dictionary"] == spook.settings.terms_dictionary.read_text(encoding="utf-8")
    assert context["transcript"].strip() == "**14:02 Собеседники:** Обсудим релиз."
    assert context["date"] == "2026-11-06"
    assert SECRET not in str(context) and str(note) not in str(context)


def test_meeting_agent_creates_terms_dictionary_template_when_file_is_missing(spook):
    dictionary = spook.settings.terms_dictionary
    assert not dictionary.exists()

    finished_meeting(spook)

    template = dictionary.read_text(encoding="utf-8")
    assert template.startswith("# Словарь терминов\n")
    assert "Агент" in template


def test_meeting_agent_leaves_existing_terms_dictionary_untouched(spook):
    dictionary = spook.settings.terms_dictionary
    write(dictionary, "- Kubernetes — кубер\n")

    finished_meeting(spook)

    assert dictionary.read_text(encoding="utf-8") == "- Kubernetes — кубер\n"
