from __future__ import annotations

def test_old_link_route_is_absent_and_note_has_no_source(spook):
    assert spook.start_meeting().status_code == 200

    response = spook.http.post(f"{spook.base_url}/session/link", data={"link": "https://example.org/talk"})

    assert response.status_code == 404
    page = spook.note_page()
    assert page.transcript == "" and not page.meta.get("source")
    assert "https://example.org/talk" not in spook.current_note().read_text(encoding="utf-8")
