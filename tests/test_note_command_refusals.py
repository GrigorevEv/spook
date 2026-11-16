from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from test_notes_screen import write_note

COMMANDS = ("obsidian", "wiki", "agent")


def reasons_of_all_commands(spook, note: Path | str) -> list[str]:
    reasons = []
    for command in COMMANDS:
        response = spook.http.post(
            f"{spook.base_url}/notes/{command}",
            data={"note": str(note), "model": "opus", "effort": "high", "confirm": "yes"},
        )
        assert response.status_code == 409, (command, note, response.text)
        reasons.append(unescape(re.fullmatch(r'<span class="error">(.*)</span>', response.text, flags=re.S).group(1)))
    return reasons


def no_program_called(spook) -> bool:
    return spook.opener.calls() == [] and spook.wiki.calls() == [] and spook.agent.calls() == []


def test_commands_with_bare_note_name_or_empty_path_are_refused_as_not_full_path(spook):
    write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")

    for note in ("2026-11-06 14-02 Лекция.md", ""):
        assert reasons_of_all_commands(spook, note) == [f"Нужен полный путь к Заметке: {note}"] * len(COMMANDS)

    assert no_program_called(spook)


def test_commands_for_file_in_kind_folder_that_is_not_session_note_are_refused(spook):
    spook.meetings_dir.mkdir(parents=True)
    terms = spook.meetings_dir / "Словарь терминов.md"
    terms.write_text("Raft\n", encoding="utf-8")

    assert reasons_of_all_commands(spook, terms) == [f"Это не Заметка Сессии: {terms}"] * len(COMMANDS)
    assert no_program_called(spook)


def test_commands_for_note_outside_kind_folders_are_refused_also_through_parent_folder(spook):
    outside = write_note(spook.lectures_dir.parent / "чужое", "2026-11-06 14-02 Чужая", "2026-11-06", "14:02", "14:47", "лекция")
    through_parent = spook.lectures_dir / ".." / "чужое" / outside.name
    assert through_parent.resolve() == outside.resolve()

    for note in (outside, through_parent):
        assert reasons_of_all_commands(spook, note) == [f"Заметка вне папок Встреч и Лекций: {note}"] * len(COMMANDS)

    assert no_program_called(spook)


def test_commands_for_missing_note_or_link_leading_outside_are_refused_as_no_note(spook, tmp_path):
    outside = write_note(tmp_path / "чужое", "2026-11-06 14-02 Чужая", "2026-11-06", "14:02", "14:47", "лекция")
    spook.lectures_dir.mkdir(parents=True)
    link = spook.lectures_dir / "2026-11-06 15-00 Ссылка наружу.md"
    link.symlink_to(outside)
    missing = spook.lectures_dir / "2026-11-06 16-00 Нет такой.md"

    for note in (missing, link):
        assert reasons_of_all_commands(spook, note) == [f"Заметки нет: {note.name}"] * len(COMMANDS)

    assert no_program_called(spook)
