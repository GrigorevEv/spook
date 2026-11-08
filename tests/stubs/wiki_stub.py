"""Заглушка инструмента Вики (`wiki_html.py`): запоминает вызов и печатает адрес страницы.

Запуск: `wiki_stub.py <каталог управления> file <имя заметки>`. Как настоящая команда `file`,
печатает `file://`-адрес страницы; страница — `<каталог управления>/html/PAGE`, имя не зависит
от имени заметки, чтобы тест видел, что адрес взят у инструмента, а не вычислен приложением.

`wiki-scenario` в каталоге управления (по умолчанию `page`):
  - `page` — страница есть: адрес в stdout, код возврата 0;
  - `missing` — страницы нет: как у настоящего инструмента, причина в stderr, адрес всё равно
    в stdout, код возврата 1.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PAGE = "stranitsa-lektsii.html"


def main() -> int:
    control = Path(sys.argv[1])
    argv = sys.argv[2:]
    with (control / "wiki-calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps({"argv": argv, "cwd": os.getcwd()}, ensure_ascii=False) + "\n")
    scenario_file = control / "wiki-scenario"
    scenario = scenario_file.read_text(encoding="utf-8").strip() if scenario_file.exists() else "page"
    page = control / "html" / PAGE
    if scenario == "missing":
        print(f"нет страницы: {argv[-1]} (html/{PAGE})", file=sys.stderr)
        print(page.as_uri())
        return 1
    print(page.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
