from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SUMMARY = "## Суть\n\nПро тесты."
TOPIC = "Репликация в Raft"


def event(kind: str, **fields) -> None:
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)


def main() -> int:
    control = Path(sys.argv[1])
    argv = sys.argv[2:]
    output = Path(argv[argv.index("--output-last-message") + 1])
    schema = Path(argv[argv.index("--output-schema") + 1])
    call = {
        "argv": argv,
        "cwd": os.getcwd(),
        "input": json.load(sys.stdin),
        "schema": json.loads(schema.read_text(encoding="utf-8")),
    }
    with (control / "codex-calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(call, ensure_ascii=False) + "\n")
    scenario_file = control / "codex-scenario"
    scenario = scenario_file.read_text(encoding="utf-8").strip() if scenario_file.exists() else "nothing"
    event("thread.started", thread_id="test")
    event("turn.started")
    if scenario == "hang":
        time.sleep(30)
    if scenario in {"wait", "wait-updated"}:
        deadline = time.monotonic() + 12
        while not (control / "codex-release").exists():
            if time.monotonic() > deadline:
                return 3
            time.sleep(0.01)
    if scenario == "fail":
        print("Сценарий: Агент упал.", file=sys.stderr)
        return 1
    if scenario in {"rate-limit", "no-network"}:
        error = "You've hit your usage limit" if scenario == "rate-limit" else "error sending request"
        event("turn.failed", error={"message": error})
        return 1
    if scenario != "nothing":
        reply = {"summary": SUMMARY, "topic": TOPIC if scenario == "topic" else ""}
        if scenario == "wait-updated":
            reply["summary"] = "## Суть\n\nВыжимка обновлена."
        if scenario == "empty":
            reply["summary"] = ""
        if scenario == "reserved-heading":
            reply["summary"] += "\n\n## Транскрипт\n\nДругая Реплика."
        if scenario == "extra-field":
            reply["other"] = "постороннее поле"
        message = "не JSON" if scenario == "malformed" else json.dumps(reply, ensure_ascii=False)
        output.write_text(message, encoding="utf-8")
        event("item.completed", item={"id": "item_0", "type": "agent_message", "text": message})
    if scenario == "error-after-summary":
        event("turn.failed", error={"message": "Сбой после ответа"})
        return 1
    event("turn.completed", usage={"input_tokens": 1, "output_tokens": 1})
    return 0


if __name__ == "__main__":
    sys.exit(main())
