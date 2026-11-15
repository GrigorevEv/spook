from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    control = Path(sys.argv[1])
    with (control / "open-calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps({"argv": sys.argv[2:], "cwd": os.getcwd()}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
