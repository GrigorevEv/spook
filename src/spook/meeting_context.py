from __future__ import annotations

import json
from pathlib import Path

from spook import wisp
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


def meeting_input(settings: Settings, note: Note, skill: str) -> str:
    if not skill or Path(skill).name != skill or skill in {".", ".."}:
        raise ValueError("Недопустимое имя Скилла")
    skill_path = settings.agent.skills_dir / skill / "SKILL.md"
    try:
        instructions = skill_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Не удалось прочитать Скилл {skill}. Проверьте {skill_path} или выполните spook install.") from error
    ensure_terms_dictionary(settings.terms_dictionary)
    return json.dumps({
        "skill": instructions,
        "date": note.date,
        "topic": note.topic,
        "terms_dictionary": settings.terms_dictionary.read_text(encoding="utf-8"),
        "wisp_notes": [
            {"title": entry["title"], "file": entry["file"]}
            for entry in wisp.index_entries(note.folder)
            if entry["file"] != note.path.name
        ],
        "transcript": note.transcript_markdown(),
    }, ensure_ascii=False)
