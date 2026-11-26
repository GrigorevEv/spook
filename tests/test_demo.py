from __future__ import annotations

import re
import tempfile
from pathlib import Path


def test_demo_plays_scripted_meeting_with_text_appearing_by_itself(demo):
    page = demo.http.get(f"{demo.base_url}/")
    assert page.status_code == 200
    assert ">Старт</button>" in page.text

    start = demo.http.post(f"{demo.base_url}/session/start")
    assert start.status_code == 200, start.text
    with demo.events() as events:
        state = events.next_event()
        first = events.wait_for("paragraph")
        stop = demo.http.post(f"{demo.base_url}/session/stop")
        assert stop.status_code == 200, stop.text
        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")
        note_path = Path(re.search(r"<code>(.+?)</code>", state.data).group(1))
        note = note_path.read_text(encoding="utf-8")

    assert state.name == "state" and "идёт" in state.data
    start_time = re.search(r'^start: "(\d{2}:\d{2})"$', note, re.M).group(1)
    assert f"<time>{start_time}</time>" in first.data
    assert "Spook слушает звук на компьютере" in first.data
    assert note_path.is_relative_to(Path(tempfile.gettempdir()))
    assert "end:" in note and f"**{start_time} Собеседники:** " in note
    assert re.findall(r"^## (.*)$", note, re.M) == ["Суть", "Решения", "Задачи", "Открытые вопросы", "Транскрипт"]
    assert demo.terminate() == 0
    assert not note_path.exists()
