from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text


def test_old_link_route_is_absent_and_note_has_no_source(spook):
    assert spook.start_meeting().status_code == 200

    response = spook.http.post(f"{spook.base_url}/session/link", data={"link": "https://example.org/talk"})

    assert response.status_code == 404
    assert note_text(spook) == NOTE_AFTER_START
