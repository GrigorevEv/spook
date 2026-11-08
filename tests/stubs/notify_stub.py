"""Заглушка программы системных уведомлений: запоминает вызов в `notify-calls.jsonl` каталога управления.

Запуск: `notify_stub.py <каталог управления> <аргументы, как у notify-send>`.
Сценарий `notify-scenario` (по умолчанию `ok`): `ok` — код возврата 0; `fail` — сообщение в stderr
и код 1, как у `notify-send` без службы уведомлений.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    control = Path(sys.argv[1])
    with (control / "notify-calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps({"argv": sys.argv[2:], "cwd": os.getcwd()}, ensure_ascii=False) + "\n")
    scenario_file = control / "notify-scenario"
    if scenario_file.exists() and scenario_file.read_text(encoding="utf-8").strip() == "fail":
        print("Заглушка уведомлений: служба уведомлений недоступна", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
