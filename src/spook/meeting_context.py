"""Что приложение даёт Агенту Встречи сверх Скилла (спецификация, «Агент» и «Настройки»).

- Названия заметок рабочего Хранилища и Вики — без содержимого, — чтобы Выжимка могла на них
  ссылаться: у Агента нет поиска по файлам (ни Glob/Grep, ни Bash), а читать всё Хранилище ему
  нельзя. Рабочее Хранилище — родитель папки Встреч, Вики — корень Вики из настроек. Читаются
  только имена `.md`; служебные каталоги (`.obsidian`, `.trash` и прочие скрытые) пропускаются.
- Путь к Словарю терминов из настроек; если файла нет, создаётся заготовка, существующий не трогается.

Всё это пишется в файл, который запуск получает флагом `--append-system-prompt-file`: так список
не упирается в длину командной строки и не требует от Скилла права читать каталог состояния.
Ссылки на заметки Вики даются готовыми (`obsidian://open?vault=<Вики>&file=…`): Вики — отдельное
Хранилище, `[[…]]` из рабочего Хранилища туда не ведёт.
"""

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
    """Создаёт заготовку Словаря терминов, если файла нет; существующий не трогает."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(TERMS_DICTIONARY_TEMPLATE)
    except FileExistsError:
        pass


def note_paths(root: Path) -> list[Path]:
    """Заметки Хранилища — пути `.md` относительно корня, без скрытых каталогов; содержимое не читается."""
    if not root.is_dir():
        return []
    found = (path.relative_to(root) for path in root.rglob("*.md") if path.is_file())
    return sorted(path for path in found if not any(part.startswith(".") for part in path.parts))


def meeting_context(settings: Settings, note_path: Path) -> str:
    """Текст для системных указаний Агента Встречи: Словарь терминов и названия заметок для ссылок."""
    vault = settings.meeting.folder.parent
    wiki = settings.wiki
    vault_names = sorted({path.stem for path in note_paths(vault)} - {note_path.stem})
    wiki_links = [_wiki_link(wiki.name, path) for path in note_paths(wiki)]
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
    """Готовит запуск Агента Встречи: заготовка Словаря терминов и файл сведений `target`."""
    ensure_terms_dictionary(settings.terms_dictionary)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(meeting_context(settings, note_path), encoding="utf-8")
    return target


def _wiki_link(vault_name: str, path: Path) -> str:
    file = quote(path.with_suffix("").as_posix(), safe="")
    return f"- [{path.stem}](obsidian://open?vault={quote(vault_name, safe='')}&file={file}) ({path.as_posix()})"
