"""Что приложение даёт Агенту Встречи сверх Скилла: названия заметок рабочего Хранилища и Вики — без
содержимого — и путь к Словарю терминов; заготовка Словаря, если файла нет."""

from __future__ import annotations

from pathlib import Path

from conftest import speech

SECRET = "пароль: hunter2"


def finished_meeting(spook) -> Path:
    """Встреча с Репликой, завершённая по «Стоп»; заглушка Агента написала Выжимку."""
    spook.agent.play("summary")
    spook.recognizer.say("Обсудим релиз.")
    assert spook.start_meeting().status_code == 200
    with spook.events() as events:
        spook.audio.feed(speech(20))
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
    wiki = spook.lectures_dir.parent
    write(wiki / "Raft.md", f"{SECRET}\n")
    write(wiki / "SCST" / "SCST.md", f"{SECRET}\n")

    note = finished_meeting(spook)

    [call] = spook.agent.calls()
    assert call["argv"][:2] == ["-p", f"/spook-meeting {note}"]
    context = call["append_system_prompt"]
    assert "2026-10-01 10-00 Планирование релиза" in context
    assert "Пароли" in context
    assert "[Raft](obsidian://open?vault=wiki&file=Raft)" in context
    assert "[SCST](obsidian://open?vault=wiki&file=SCST%2FSCST)" in context
    assert str(spook.settings.terms_dictionary) in context
    assert SECRET not in context
    assert "Служебное" not in context and "схема" not in context
    assert note.stem not in context  # сама Заметка — не связанная


def test_lecture_agent_gets_no_list_of_notes(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        events.wait_for("agent", containing="готово")

    [call] = spook.agent.calls()
    assert "--append-system-prompt-file" not in call["argv"]


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
