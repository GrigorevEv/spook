from __future__ import annotations

import json
import re
import subprocess
import sys

from conftest import speech
from wisp_page import read_page


def entries(spook):
    index = (spook.meetings_dir / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'<script type="application/json" id="wiki-index">(.*?)</script>', index, re.S)[1])


def capture(spook, topic=""):
    assert spook.start_meeting(topic).status_code == 200
    text = "Текст <script> & пример."
    spook.recognizer.say(text)
    spook.feed_system(speech(20))
    spook.wait_until(lambda: text in spook.note_page().transcript)


def test_stop_publishes_transcript_before_codex_and_summary_uses_same_html_page(spook, tmp_path):
    previous = tmp_path / "старые заметки" / "Встреча.md"
    previous.parent.mkdir()
    previous.write_text("Старая Встреча", encoding="utf-8")
    spook.write_settings(meeting={"folder": str(previous.parent)})
    spook.agent.play("wait")
    capture(spook)
    assert entries(spook) == []
    assert spook.pages() == []

    try:
        assert spook.stop().status_code == 200
        spook.wait_until(lambda: len(spook.agent.calls()) == 1)
        [item] = entries(spook)
        path = spook.meetings_dir / item["file"]
        assert item["section"] == "Встречи" and path.suffix == ".html"
        initial = read_page(path)
        assert "Текст <script> & пример." in initial.transcript and not initial.summary
        assert "&lt;script&gt; &amp;" in path.read_text(encoding="utf-8")
    finally:
        spook.agent.release()

    spook.wait_until(lambda: 'data-summary="done"' in spook.page().text)
    assert read_page(path).summary
    assert [entry["file"] for entry in entries(spook)] == [path.name]
    assert read_page(path).transcript == initial.transcript
    assert previous.read_text(encoding="utf-8") == "Старая Встреча"
    assert list(previous.parent.iterdir()) == [previous]
    assert not list(spook.wisp_dir.rglob("*.md"))


def test_failed_index_retries_same_page_and_starts_codex_once(spook):
    spook.agent.play("summary")
    capture(spook, "Повтор")
    script = spook.wisp_dir / "scripts/wiki_html.py"
    native = script.read_bytes()
    script.write_text("raise SystemExit(5)\n", encoding="utf-8")
    try:
        spook.stop()
        spook.wait_until(lambda: "Повторить сохранение" in spook.page().text)
        assert not spook.agent.calls() and not entries(spook)
        [page] = spook.pages()
    finally:
        script.write_bytes(native)

    assert spook.stop().status_code == 200
    spook.wait_until(lambda: 'data-summary="done"' in spook.page().text)
    assert read_page(page).summary
    assert [entry["file"] for entry in entries(spook)] == [page.name]
    assert len(spook.agent.calls()) == 1
    assert not list(spook.drafts_dir.glob("*.html"))

def test_published_title_respects_an_existing_wisp_alias(spook):
    spook.agent.play("summary")
    capture(spook, "Первая")
    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="готово")
    [first] = spook.pages()
    page = first.read_text(encoding="utf-8")
    meta = read_page(first).meta
    meta["aliases"] = ["2026-11-06 14-02 План"]
    page = re.sub(r'(<script type="application/json" id="wiki-meta">).*?(</script>)',
                  lambda m: m[1] + json.dumps(meta, ensure_ascii=False) + m[2], page, count=1, flags=re.S)
    first.write_text(page, encoding="utf-8")
    subprocess.run([sys.executable, str(spook.wisp_dir / "scripts/wiki_html.py"), "index", "--html-only"], check=True)

    capture(spook, "План")
    with spook.events() as events:
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    spook.wait_until(lambda: spook.page().text.count('data-summary="done"') == 2)
    assert sorted(item["title"] for item in entries(spook)) == ["2026-11-06 14-02 Первая", "2026-11-06 14-02 План (2)"]
    assert first.read_text(encoding="utf-8") == page
