import pytest


def existing_note(spook):
    spook.meetings_dir.mkdir(parents=True, exist_ok=True)
    note = spook.meetings_dir / "2026-11-06 14-02 Планирование.md"
    properties = '---\r\ndate: 2026-11-06\r\nstart: "14:02"\r\nend: "15:07"\r\ntags: [встреча]\r\n---\r\n'
    transcript = "## Транскрипт\r\n\r\n**14:02 Я:** Первая Реплика.\r\n"
    note.write_bytes((properties + "\r\nВыжимка до перезапуска.\r\n\r\n" + transcript).encode())
    return note, properties.encode(), transcript.encode()


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
    note, _, _ = existing_note(spook)
    before = note.read_bytes()

    run_codex(spook, note, scenario, "ошибка")

    assert note.read_bytes() == before
    assert list(note.parent.glob("2026-11-06 *.md")) == [note]


def test_structured_reply_preserves_existing_topic_and_exact_transcript_bytes(spook):
    note, properties, transcript = existing_note(spook)

    run_codex(spook, note, "topic", "готово")

    contents = note.read_bytes()
    assert contents.startswith(properties)
    assert contents.endswith(transcript)
    assert "Про тесты." in contents.decode()
    assert "Выжимка до перезапуска." not in contents.decode()
    assert list(note.parent.glob("2026-11-06 *.md")) == [note]
