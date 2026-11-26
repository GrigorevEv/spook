from __future__ import annotations

import json
import re
from html import escape, unescape
from pathlib import Path
from shutil import copyfile

_INDEX = '<script type="application/json" id="wiki-index">[]</script>'
_SECTIONS = '<script type="application/json" id="wiki-sections">[{"t":"Встречи","subs":[]}]</script>'
_META = re.compile(r'<script type="application/json" id="wiki-meta">(.*?)</script>', re.S)


def demo_wisp(root: Path) -> Path:
    for name in ("html", "_templates", "scripts"):
        (root / name).mkdir(parents=True, exist_ok=True)
    template = root / "_templates" / "Заметка.html"
    template.write_text('<style id="wiki-css">body{font:1rem system-ui;max-width:60rem;margin:auto}</style>'
                        '<script id="wiki-engine"></script>', encoding="utf-8")
    (root / "html" / "index.html").write_text(_index([], ""), encoding="utf-8")
    copyfile(__file__, root / "scripts" / "wiki_html.py")
    return root


def _index(entries: list[dict], links: str) -> str:
    data = json.dumps(entries, ensure_ascii=False).replace("<", r"\u003c")
    return (f'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Встречи</title>'
            f'{_SECTIONS}{_INDEX.replace("[]", data)}<h1>Встречи</h1><ul>{links}</ul></html>')


def _refresh(root: Path) -> None:
    folder = root / "html"
    entries = []
    links = []
    for page in sorted(folder.glob("*.html")):
        if page.name == "index.html":
            continue
        source = page.read_text(encoding="utf-8")
        match = _META.search(source)
        if match:
            meta = json.loads(match[1])
            description = re.search(r'<meta name="description" content="([^"]*)">', source)
            entries.append({"title": meta["title"], "file": page.name, "section": meta["section"],
                            "aliases": meta.get("aliases", []),
                            "summary": unescape(description[1]) if description else ""})
            links.append(f'<li><a href="{escape(page.name)}">{escape(meta["title"])}</a></li>')
    (folder / "index.html").write_text(_index(entries, "".join(links)), encoding="utf-8")


if __name__ == "__main__":
    _refresh(Path(__file__).resolve().parents[1])
