from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from html import escape, unescape
from pathlib import Path
from stat import S_IMODE

SECTION = "Встречи"
_RUSSIAN = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"),
))
_INDEX = re.compile(r'<script type="application/json" id="wiki-index">(.*?)</script>', re.S)
_PAGE_FILE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.html")
_META = re.compile(r'<script type="application/json" id="wiki-meta">(.+?)</script>', re.S)
_SECTIONS = re.compile(r'<script type="application/json" id="wiki-sections">(.*?)</script>', re.S)
_STYLES = re.compile(r'<style id="wiki-css">.*?</style>', re.S)
_ENGINE = re.compile(r'<script id="wiki-engine">.*?</script>', re.S)


def page_filename(title: str) -> str:
    latin = "".join(_RUSSIAN.get(ch, ch) for ch in title.lower())
    return (re.sub("[^a-z0-9]+", "-", latin).strip("-") or "page") + ".html"


def page_meta(page: str) -> dict:
    match = _META.search(page)
    if match:
        try:
            meta = json.loads(match[1])
        except ValueError:
            return {}
        if isinstance(meta, dict):
            return meta
    return {}


def index_entries(folder: Path) -> list[dict]:
    index = (folder / "index.html").read_text(encoding="utf-8")
    _require_meetings_section(index)
    match = _INDEX.search(index)
    try:
        entries = json.loads(match[1]) if match else None
    except ValueError as error:
        raise OSError("Не читается список страниц Wisp") from error
    if not isinstance(entries, list):
        raise OSError("В Wisp отсутствует список страниц")
    return [entry for entry in entries if isinstance(entry, dict)
            and isinstance(entry.get("title"), str) and isinstance(entry.get("file"), str)
            and _PAGE_FILE.fullmatch(entry["file"]) and entry["file"] != "index.html"]


def json_html(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", r"\u003c")


def with_meta(page: str, meta: dict) -> str:
    match = _META.search(page)
    if match is None:
        raise OSError("В HTML-Заметке отсутствуют свойства Wisp")
    return page[:match.start(1)] + json_html(meta) + page[match.end(1):]


def _require_meetings_section(index: str) -> None:
    match = _SECTIONS.search(index)
    try:
        sections = json.loads(match[1]) if match else []
    except ValueError as error:
        raise OSError("Не читается список разделов Wisp") from error
    if not isinstance(sections, list) or not any(isinstance(s, dict) and s.get("t") == SECTION for s in sections):
        raise OSError("В Wisp нет раздела «Встречи»")


def page_style(folder: Path) -> tuple[str, str]:
    root = folder.parent
    _require_meetings_section((folder / "index.html").read_text(encoding="utf-8"))
    if not (root / "scripts" / "wiki_html.py").is_file():
        raise OSError("Не найден инструмент Wisp scripts/wiki_html.py")
    template = (root / "_templates" / "Заметка.html").read_text(encoding="utf-8")
    css, engine = _STYLES.search(template), _ENGINE.search(template)
    if not css or not engine:
        raise OSError("В шаблоне Wisp отсутствует оформление страницы")
    return css[0], engine[0]


def refresh_index(page: Path) -> None:
    folder = page.parent
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(folder.parent / "scripts" / "wiki_html.py"), "index", "--html-only"],
            cwd=folder.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=60, check=False, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("Wisp не успел обновить раздел «Встречи»") from error
    if result.returncode:
        reason = result.stderr.strip().splitlines()[-1:] or [f"код {result.returncode}"]
        raise OSError(f"Wisp не обновил раздел «Встречи»: {reason[0]}")
    if not any(entry.get("section") == SECTION and entry["file"] == page.name for entry in index_entries(folder)):
        raise OSError("Заметка отсутствует в разделе «Встречи» Wisp")


@contextmanager
def write_lock(folder: Path):
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def write_page(path: Path, page: str, *, create: bool = False) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".spook-", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(page.encode("utf-8"))
            file.flush()
            if path.parent.name == "html":
                mode_source = path if not create and path.exists() else path.parent / "index.html"
                os.fchmod(file.fileno(), S_IMODE(mode_source.stat().st_mode) & 0o666)
            if create:
                os.link(temporary, path)
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def section(page: str, name: str) -> tuple[int, int]:
    opening = f'<section class="card" id="{name}">'
    position = page.find(opening)
    if position == -1:
        raise OSError(f"В HTML-Заметке отсутствует раздел {name}")
    start = page.find("</h2>", position + len(opening))
    end = page.find("</section>", start)
    if start == -1 or end == -1:
        raise OSError(f"Повреждён раздел {name} в HTML-Заметке")
    return start + len("</h2>"), end


def content(page: str, name: str) -> str:
    start, end = section(page, name)
    return page[start:end].strip()


def with_content(page: str, name: str, body: str) -> str:
    start, end = section(page, name)
    return page[:start] + "\n" + body.strip() + "\n" + page[end:]


def text_content(markup: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", markup)).strip()


def with_title(page: str, name: str) -> str:
    title = escape(name)
    for tag in ("title", "h1"):
        page = re.sub(rf"<{tag}>.*?</{tag}>", lambda _: f"<{tag}>{title}</{tag}>", page, count=1, flags=re.S)
    return page
