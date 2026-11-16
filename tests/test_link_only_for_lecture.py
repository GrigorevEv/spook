from __future__ import annotations

from conftest import MEETING_NOTE_AFTER_START, meeting_note_text


def test_link_change_during_meeting_is_rejected_with_reason_and_note_gets_no_source(spook):
    assert spook.start_meeting().status_code == 200

    response = spook.set_link("https://example.org/talk")

    assert response.status_code == 409
    assert "Ссылка — только у Лекции" in response.text
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START
