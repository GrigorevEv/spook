from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

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


def meeting_context(settings: Settings, note_path: Path) -> str:
    vault = settings.meeting.folder.parent
    wiki = settings.wiki
    vault_names = sorted({path.stem for path in note_paths(vault)} - {note_path.stem})
    wiki_links = [_cross_vault_link(wiki.name, path) for path in note_paths(wiki)]
    lines = [
        "# Сведения от Spook",
        "",
        f"Словарь терминов: {settings.terms_dictionary}",
        "",
        f"## Заметки рабочего Хранилища ({vault})",
        "",
        "Только названия, содержимого здесь нет. Ссылка — `[[Название]]`.",
        "",
        *(f"- {name}" for name in vault_names),
        "",
        f"## Заметки Вики ({wiki})",
        "",
        "Ссылка — готовая, как в списке; путь к файлу заметки — в скобках после ссылки.",
        "",
        *wiki_links,
        "",
    ]
    return "\n".join(lines)


def write_meeting_context(settings: Settings, note_path: Path, target: Path) -> Path:
    ensure_terms_dictionary(settings.terms_dictionary)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(meeting_context(settings, note_path), encoding="utf-8")
    return target


def _cross_vault_link(vault_name: str, path: Path) -> str:
    file = quote(path.with_suffix("").as_posix(), safe="")
    return f"- [{path.stem}](obsidian://open?vault={quote(vault_name, safe='')}&file={file}) ({path.as_posix()})"
