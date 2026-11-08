"""Заглушка Claude Code для тестов: запоминает вызов и играет сценарий.

Запуск: `claude_stub.py <каталог управления> <аргументы, как у настоящей программы>`.
Каталог управления — временный каталог теста; в нём:

- `claude-calls.jsonl` — по строке на вызов: аргументы, рабочий каталог и — если передан файл
  `--append-system-prompt-file` — его текст на момент вызова (`append_system_prompt`);
- `claude-scenario` — сценарий (по умолчанию `nothing`):
  - `summary` — дописать Выжимку над «## Транскрипт», код возврата 0;
  - `nothing` — ничего не писать, код возврата 0;
  - `fail` — сообщение в stderr, код возврата 1;
  - `wait` — ждать файла `claude-release` (не дольше 10 секунд), удалить его и сыграть `summary`;
  - `rewrite-transcript` — как `summary`, но текст ниже «## Транскрипт» переписан (порча Транскрипта);
  - `erase-heading` — как `summary`, но строка «## Транскрипт» стёрта, текст под ней оставлен;
  - `delete-note` — удалить файл Заметки, код возврата 0;
  - `spoil-and-hang` — как `rewrite-transcript`, но после записи висеть 30 секунд: запуск,
    который застанет перезапуск службы;
  - `topic` — как Скилл у Заметки без Темы: сообщить Тему `TOPIC` командой приложения
    `spook topic <путь> <Тема>` (службу команда находит по настройкам, как у пользователя), дальше
    работать с напечатанным новым путём и сыграть `summary`; отказ команды — её причина в stderr
    и её код возврата.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "claude"
SUMMARY = "## О чём\n\nПро тесты.\n"
TRANSCRIPT_HEADING = "## Транскрипт"
TOPIC = "Репликация в Raft"


def main() -> int:
    control = Path(sys.argv[1])
    argv = sys.argv[2:]
    call = {"argv": argv, "cwd": os.getcwd()}
    if "--append-system-prompt-file" in argv:
        appended = Path(argv[argv.index("--append-system-prompt-file") + 1])
        call["append_system_prompt"] = appended.read_text(encoding="utf-8")
    with (control / "claude-calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(call, ensure_ascii=False) + "\n")

    scenario_file = control / "claude-scenario"
    scenario = scenario_file.read_text(encoding="utf-8").strip() if scenario_file.exists() else "nothing"
    if scenario.startswith("replay "):
        return replay(argv, *scenario.split()[1:])
    print(f"Заглушка Агента: сценарий «{scenario}».")
    if scenario == "hang":
        time.sleep(30)
        return 0

    if scenario == "wait":
        release = control / "claude-release"
        deadline = time.monotonic() + 10
        while not release.exists():
            if time.monotonic() > deadline:
                print("Заглушка Агента: не дождалась отпускания.", file=sys.stderr)
                return 3
            time.sleep(0.01)
        release.unlink()
        scenario = "summary"

    if scenario == "fail":
        print("Сценарий: Агент упал.", file=sys.stderr)
        return 1
    if scenario == "nothing":
        print("Заглушка Агента: ничего не написала.")
        return 0

    note = note_path(argv)
    if scenario == "delete-note":
        note.unlink()
        print("Заглушка Агента: Заметка удалена.")
        return 0
    if scenario == "topic":
        named = subprocess.run(
            [sys.executable, "-m", "spook.cli", "topic", str(note), TOPIC], capture_output=True, encoding="utf-8"
        )
        if named.returncode != 0:
            print(f"Заглушка Агента: команда Темы отклонена: {named.stderr.strip()}", file=sys.stderr)
            return named.returncode
        note = Path(named.stdout.strip())
        print(f"Заглушка Агента: Тема сообщена, Заметка теперь {note}.")

    text = with_summary(note.read_text(encoding="utf-8"))
    print("Заглушка Агента: Выжимка написана.")
    if scenario in ("rewrite-transcript", "spoil-and-hang"):
        above, heading, _ = text.partition(TRANSCRIPT_HEADING)
        text = f"{above}{heading}\n\n**0:00:00** Транскрипт переписан Агентом.\n"
        print("Заглушка Агента: Транскрипт переписан.")
    elif scenario == "erase-heading":
        text = text.replace(f"{TRANSCRIPT_HEADING}\n", "", 1)
        print("Заглушка Агента: заголовок «Транскрипт» стёрт.")
    note.write_text(text, encoding="utf-8")
    if scenario == "spoil-and-hang":
        sys.stdout.flush()
        time.sleep(30)
    return 0


def note_path(argv: list[str]) -> Path:
    prompt = argv[argv.index("-p") + 1]
    return Path(prompt.partition(" ")[2])


def with_summary(text: str) -> str:
    return text.replace(TRANSCRIPT_HEADING, f"{SUMMARY}\n{TRANSCRIPT_HEADING}", 1)


def replay(argv: list[str], sample: str, returncode: str, *rest: str) -> int:
    sys.stdout.write((SAMPLES_DIR / f"{sample}.jsonl").read_text(encoding="utf-8"))
    stderr = SAMPLES_DIR / f"{sample}.stderr"
    if stderr.exists():
        sys.stderr.write(stderr.read_text(encoding="utf-8"))
    if "summary" in rest:
        note = note_path(argv)
        note.write_text(with_summary(note.read_text(encoding="utf-8")), encoding="utf-8")
    return int(returncode)


if __name__ == "__main__":
    sys.exit(main())
