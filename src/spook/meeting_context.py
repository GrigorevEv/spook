from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from spook.note import Note
from spook.settings import Settings

TERMS_DICTIONARY_TEMPLATE = """\
# Словарь терминов

Правильные написания терминов, названий продуктов и имён коллег. Агент Spook сверяется со Словарём,
когда пишет Выжимку Встречи: распознавание речи искажает английские слова, названия и фамилии.

Пишите по строке на термин: правильное написание, после тире — как его слышно или как его
искажает распознавание (необязательно). Например: `Kubernetes — кубер, кубернетис`.

"""


def ensure_terms_dictionary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(TERMS_DICTIONARY_TEMPLATE)
    except FileExistsError:
        pass


def note_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = (path.relative_to(root) for path in root.rglob("*.md") if path.is_file())
    return sorted(path for path in found if not any(part.startswith(".") for part in path.parts))


def meeting_input(settings: Settings, note: Note, skill: str) -> str:
    if not skill or Path(skill).name != skill or skill in {".", ".."}:
        raise ValueError("Недопустимое имя Скилла")
    skill_path = settings.agent.skills_dir / skill / "SKILL.md"
    try:
        instructions = skill_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Не удалось прочитать Скилл {skill}. Проверьте {skill_path} или выполните spook install.") from error
    ensure_terms_dictionary(settings.terms_dictionary)
    vault = settings.meeting.folder.parent
    wiki = settings.wiki
    return json.dumps({
        "skill": instructions,
        "date": note.date,
        "topic": note.topic,
        "terms_dictionary": settings.terms_dictionary.read_text(encoding="utf-8"),
        "vault_notes": sorted({path.stem for path in note_paths(vault)} - {note.path.stem}),
        "wiki_notes": [_cross_vault_link(wiki.name, path) for path in note_paths(wiki)],
        "transcript": note.transcript_markdown(),
    }, ensure_ascii=False)


def _cross_vault_link(vault_name: str, path: Path) -> str:
    file = quote(path.with_suffix("").as_posix(), safe="")
    return f"[{path.stem}](obsidian://open?vault={quote(vault_name, safe='')}&file={file})"
