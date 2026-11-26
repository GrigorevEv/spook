from __future__ import annotations

import re
import tempfile
from pathlib import Path

from wisp_page import read_page


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
        done = events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")
        draft = Path(re.search(r"<code>(.+?)</code>", state.data)[1])
        note_path = Path(re.search(r"<code>(.+?)</code>", done.data)[1])
        note = read_page(note_path)

    assert state.name == "state" and "идёт" in state.data
    start_time = note.properties["start"]
    assert f"<time>{start_time}</time>" in first.data
    assert "Spook слушает звук на компьютере" in first.data
    assert note_path.is_relative_to(Path(tempfile.gettempdir()))
    assert draft.parent.name == ".spook" and not draft.exists()
    assert note_path.parent.name == "html" and note_path.suffix == ".html"
    assert re.fullmatch(r"\d{2}:\d{2}", note.properties["end"])
    assert f"{start_time} Собеседники: " in note.transcript
    assert re.findall(r"^(Суть|Решения|Задачи|Открытые вопросы)$", note.summary, re.M) == [
        "Суть", "Решения", "Задачи", "Открытые вопросы",
    ]
    assert demo.terminate() == 0
    assert not note_path.exists()
