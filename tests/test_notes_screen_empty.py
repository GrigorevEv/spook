from __future__ import annotations


def test_notes_screen_without_session_notes_says_sessions_will_appear_after_first_session(spook):
    (spook.meetings_dir / "Словарь терминов.md").write_text("Raft\n", encoding="utf-8")
    (spook.meetings_dir / "Планы на год.md").write_text("Планы.\n", encoding="utf-8")

    (spook.meetings_dir / "foreign.html").write_text("<h1>Индекс проекта</h1>", encoding="utf-8")

    response = spook.http.get(f"{spook.base_url}/notes")

    assert response.status_code == 200
    assert "Сессий пока нет: Заметки появятся здесь после первой Сессии." in response.text
    assert '<table id="notes">' not in response.text and '<tr class="note"' not in response.text
