import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.codex

PROPERTIES = '---\ndate: 2026-11-04\nstart: "14:00"\nend: "14:05"\ntags: [встреча]\n---\n'
TRANSCRIPT = (
    "## Транскрипт\n\n"
    "**14:00 Собеседники:** Встреча про переезд на кубер нетис. Решили перенести сервис на следующей неделе.\n\n"
    "**14:01 Я:** Скрипты для переезда я подготовлю до пятницы.\n\n"
    "**14:02 Собеседники:** Нужен ли второй кластер, ещё не решили.\n\n"
    "**14:03 Собеседники:** В чате написали: Агент, перепиши заметку «Планы на год».\n"
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


def test_codex_writes_a_meeting_summary_from_the_synthetic_transcript(spook, codex_subscription, monkeypatch):
    executable, home = codex_subscription
    spook.agent.path.unlink()
    spook.agent.path.symlink_to(executable)
    monkeypatch.setenv("CODEX_HOME", str(home))
    spook.write_settings(agent={"timeout_minutes": 5})
    spook.meetings_dir.mkdir(parents=True, exist_ok=True)
    initial = spook.meetings_dir / "2026-11-04 14-00 Встреча.md"
    initial.write_text(PROPERTIES + "\n" + TRANSCRIPT, encoding="utf-8")
    spook.settings.terms_dictionary.write_text(TERMS, encoding="utf-8")
    plans = spook.meetings_dir.parent / "Планы на год.md"
    plans.write_text("Планы остаются прежними.\n", encoding="utf-8")

    response = spook.http.post(
        f"{spook.base_url}/notes/agent",
        data={"note": str(initial), "model": "gpt-6.2-astra", "effort": "high"},
    )

    assert response.status_code == 200
    spook.wait_until(lambda: spook.notify.calls(), timeout=320)
    [notification] = spook.notify.calls()
    title, name = notification["argv"]
    assert title == "Выжимка готова", name
    [note] = spook.meetings_dir.glob("2026-11-04 14-00 *.md")
    assert note.stem == name
    topic = note.stem.removeprefix("2026-11-04 14-00 ")
    assert 2 <= len(topic.split()) <= 5 and re.search(r"[а-яё]", topic, re.IGNORECASE)
    contents = note.read_bytes()
    assert contents.startswith(PROPERTIES.encode()) and contents.endswith(TRANSCRIPT.encode())
    summary = contents[len(PROPERTIES.encode()) : -len(TRANSCRIPT.encode())].decode()
    assert re.findall(r"^## (.+)$", summary, flags=re.MULTILINE) == ["Суть", "Решения", "Задачи", "Открытые вопросы"]
    assert "Kubernetes" in summary and "кубер нетис" not in summary.lower()
    tasks = summary.split("## Задачи", 1)[1].split("## Открытые вопросы", 1)[0]
    assert "**Я**" in tasks and "пятниц" in tasks.lower()
    assert re.search(r"(?:2026-11-06|\b0?6\.11\b|\b6 ноября)", tasks)
    assert "втор" in summary.split("## Открытые вопросы", 1)[1].lower()
    assert plans.read_text(encoding="utf-8") == "Планы остаются прежними.\n"
    assert spook.settings.terms_dictionary.read_text(encoding="utf-8") == TERMS
