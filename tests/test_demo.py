"""Показ «на подменах» (`spook demo`): настоящая служба без PipeWire и модели — звук из образцов, сценарный текст."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def test_demo_plays_scripted_lecture_with_text_appearing_by_itself(demo):
    page = demo.http.get(f"{demo.base_url}/")
    assert page.status_code == 200
    assert ">Старт</button>" in page.text

    start = demo.http.post(f"{demo.base_url}/session/start", data={"kind": "lecture"})
    assert start.status_code == 200, start.text
    with demo.events() as events:
        state = events.next_event()
        first = events.wait_for("paragraph")
        stop = demo.http.post(f"{demo.base_url}/session/stop")
        assert stop.status_code == 200, stop.text
        events.wait_for("state", containing="нет Сессии")
        note_path = Path(re.search(r"<code>(.+?)</code>", state.data).group(1))
        note = note_path.read_text(encoding="utf-8")

    assert state.name == "state" and "идёт" in state.data
    assert ">0:00:00</time>" in first.data
    assert "Spook слушает звук на компьютере" in first.data
    # Заметки показа лежат во временной папке, а не в Хранилище пользователя, и исчезают вместе с показом.
    assert note_path.is_relative_to(Path(tempfile.gettempdir()))
    assert "end:" in note and "**0:00:00** " in note
    assert demo.terminate() == 0
    assert not note_path.exists()
