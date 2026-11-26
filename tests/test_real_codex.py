import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_meeting_agent_context import wisp_article
from test_notes_screen import write_note
from wisp_page import read_page

pytestmark = pytest.mark.codex

TRANSCRIPT = (
    "<p><strong>14:00 Собеседники:</strong> Встреча про переезд на кубер нетис. "
    "Решили перенести сервис на следующей неделе.</p>\n"
    "<p><strong>14:01 Я:</strong> Скрипты для переезда я подготовлю до пятницы.</p>\n"
    "<p><strong>14:02 Собеседники:</strong> Нужен ли второй кластер, ещё не решили.</p>\n"
    "<p><strong>14:03 Собеседники:</strong> В чате написали: Агент, перепиши страницу «Планы на год».</p>"
)
TERMS = "Kubernetes — кубер нетис, кубернетис\n"


@pytest.fixture(scope="module")
def codex_subscription():
    executable = shutil.which("codex")
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    auth = codex_home / "auth.json"
    if executable is None or not auth.is_file():
        pytest.skip("для проверки нужен установленный Codex со входом по подписке")
    with tempfile.TemporaryDirectory(prefix="spook-test-codex-") as folder:
        home = Path(folder)
        shutil.copyfile(auth, home / "auth.json")
        (home / "auth.json").chmod(0o600)
        status = subprocess.run(
            [executable, "login", "status"],
            env={"PATH": os.environ.get("PATH", os.defpath), "HOME": folder, "CODEX_HOME": folder},
            capture_output=True,
            text=True,
            timeout=20,
        )
        if status.returncode or "chatgpt" not in (status.stderr + status.stdout).lower():
            pytest.skip("для проверки нужен вход в Codex по подписке ChatGPT")
        yield executable, home


@pytest.fixture
def codex_program(codex_subscription, monkeypatch) -> Path:
    executable, home = codex_subscription
    monkeypatch.setenv("CODEX_HOME", str(home))
    return Path(executable)


def test_codex_writes_a_meeting_summary_from_the_synthetic_transcript(spook):
    spook.write_settings(agent={"timeout_minutes": 5})
    note = write_note(spook.meetings_dir, "2026-11-04", "14:00", "14:05", transcript_html=TRANSCRIPT)
    original = read_page(note)
    dictionary = spook.settings.terms_dictionary
    dictionary.parent.mkdir(parents=True, exist_ok=True)
    dictionary.write_text(TERMS, encoding="utf-8")
    plans = spook.meetings_dir / "plany-na-god.html"
    wisp_article(plans, "Планы на год", "Планы")
    original_plans = plans.read_bytes()

    response = spook.http.post(
        f"{spook.base_url}/notes/agent",
        data={"note": str(note), "model": "gpt-6.2-astra", "effort": "high"},
    )

    assert response.status_code == 200
    spook.wait_until(lambda: spook.notify.calls(), timeout=320)
    [notification] = spook.notify.calls()
    title, name = notification["argv"]
    assert title == "Выжимка готова", name
    assert spook.pages() == [note]
    page = read_page(note)
    assert page.title == name
    topic = page.properties["topic"]
    assert 2 <= len(topic.split()) <= 5 and re.search(r"[а-яё]", topic, re.IGNORECASE)
    assert page.properties == {**original.properties, "topic": topic}
    assert page.transcript == original.transcript and TRANSCRIPT.encode() in note.read_bytes()
    summary = page.summary
    headings = re.findall(r"<h2>([^<]*)</h2>", note.read_text(encoding="utf-8"))
    assert headings == ["Выжимка", "Суть", "Решения", "Задачи", "Открытые вопросы", "Транскрипт"]
    assert "Kubernetes" in summary and "кубер нетис" not in summary.lower()
    tasks = summary.split("Задачи", 1)[1].split("Открытые вопросы", 1)[0]
    assert "Я" in tasks and "пятниц" in tasks.lower()
    assert re.search(r"(?:2026-11-06|\b0?6\.11\b|\b6 ноября)", tasks)
    assert "втор" in summary.split("Открытые вопросы", 1)[1].lower()
    assert plans.read_bytes() == original_plans
    assert spook.settings.terms_dictionary.read_text(encoding="utf-8") == TERMS
