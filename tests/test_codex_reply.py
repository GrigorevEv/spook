import pytest

from test_meeting_agent_context import wisp_article
from test_notes_screen import write_note
from wisp_page import read_page

TRANSCRIPT = '<p><strong>14:02 Я:</strong> Первая Реплика &amp; слово &lt;первое&gt;.</p>'


def existing_note(spook):
    return write_note(
        spook.meetings_dir, "2026-11-06", "14:02", "15:07", "Планирование",
        summary="<p>Выжимка до перезапуска.</p>", transcript_html=TRANSCRIPT,
    )


def run_codex(spook, note, scenario, outcome):
    spook.agent.play(scenario)
    with spook.events() as events:
        response = spook.http.post(
            f"{spook.base_url}/notes/agent",
            data={"note": str(note), "model": "gpt-6.2-astra", "effort": "high", "confirm": "yes"},
        )
        assert response.status_code == 200
        return events.wait_for("agent", containing=outcome).data


@pytest.mark.parametrize("scenario", ["extra-field", "reserved-heading", "error-after-summary"])
def test_invalid_result_leaves_existing_note_intact(spook, scenario):
    note = existing_note(spook)
    before = note.read_bytes()

    run_codex(spook, note, scenario, "ошибка")

    assert note.read_bytes() == before
    assert spook.pages() == [note]


def test_structured_reply_preserves_the_topic_and_the_escaped_html_transcript(spook):
    note = existing_note(spook)
    before = read_page(note)

    run_codex(spook, note, "topic", "готово")

    page = read_page(note)
    assert page.title == before.title
    assert page.properties == before.properties
    assert page.transcript == before.transcript
    assert TRANSCRIPT.encode() in note.read_bytes()
    assert page.summary == "Суть\n\nПро тесты."
    assert spook.pages() == [note]


def test_spook_escapes_html_in_agent_text_and_renders_a_relative_wisp_link(spook):
    note = existing_note(spook)
    article = spook.meetings_dir / "plan-reliza.html"
    wisp_article(article, "План релиза", "Планы")

    run_codex(spook, note, "html-summary", "готово")

    page = read_page(note)
    assert "<mark>Текст разговора</mark> и план." in page.summary
    markup = note.read_text(encoding="utf-8")
    assert "&lt;mark&gt;Текст разговора&lt;/mark&gt;" in markup
    assert "<mark>Текст разговора</mark>" not in markup
    assert '<a href="plan-reliza.html">план</a>' in markup
