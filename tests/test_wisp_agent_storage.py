import json
import re
import subprocess
import sys
from uuid import uuid4

from conftest import speech
from wisp_page import read_page

UTTERANCE = "Закрытая Реплика о квартальном плане."


def finish_meeting(spook, events):
    spook.recognizer.say(UTTERANCE)
    assert spook.start_meeting().status_code == 200
    spook.feed_system(speech(20))
    events.wait_for("paragraph", containing=UTTERANCE)
    assert spook.stop().status_code == 200


def journal(spook, page):
    response = spook.http.get(f"{spook.base_url}/agent/log", params={"note": page.stem, "kind": "meeting"})
    assert response.status_code == 200
    return response.text


def test_active_guard_is_only_in_wisp_and_running_queue_contains_its_identifier(spook):
    spook.agent.play("wait")
    with spook.events() as events:
        finish_meeting(spook, events)
        spook.wait_until(lambda: bool(spook.agent.calls()))
        [page] = spook.pages()
        queue = spook.state_dir / "agent" / "queue.json"
        [record] = json.loads(queue.read_text(encoding="utf-8"))
        identifier = record["snapshot_id"]
        assert re.fullmatch("[0-9a-f]{32}", identifier)
        snapshot = spook.wisp_dir / ".spook" / f"agent-{identifier}.guard"
        assert UTTERANCE in snapshot.read_text(encoding="utf-8")
        assert UTTERANCE not in queue.read_text(encoding="utf-8")
        page.write_text(page.read_text(encoding="utf-8").replace(UTTERANCE, "Подменённая Реплика."), encoding="utf-8")
        spook.agent.release()
        events.wait_for("agent", containing="готово")

    assert UTTERANCE in read_page(page).transcript
    assert "Про тесты." in read_page(page).summary
    assert not snapshot.exists()
    assert not snapshot.with_suffix(".json").exists()
    assert json.loads(queue.read_text(encoding="utf-8")) == []
    saved = journal(spook, page)
    assert "Охрана Транскрипта" in saved
    assert "item.completed" in saved
    assert UTTERANCE not in saved and "Про тесты." not in saved


def test_interrupted_agent_restores_snapshot_from_wisp_after_restart(spook):
    spook.agent.play("summary")
    with spook.events() as events:
        finish_meeting(spook, events)
        events.wait_for("agent", containing="готово")
    [page] = spook.pages()
    identifier = uuid4().hex
    snapshot = spook.wisp_dir / ".spook" / f"agent-{identifier}.guard"
    snapshot.write_bytes(page.read_bytes())
    page.unlink()
    index = spook.meetings_dir / "index.html"
    subprocess.run(
        [sys.executable, "-I", str(spook.wisp_dir / "scripts/wiki_html.py"), "index", "--html-only"],
        cwd=spook.wisp_dir, check=True,
    )
    assert page.name not in index.read_text(encoding="utf-8")
    queue = spook.state_dir / "agent" / "queue.json"
    queue.write_text(json.dumps([{
        "note": str(page), "state": "running", "snapshot_id": identifier, "kind": "meeting",
        "skill": "spook-meeting", "model": "gpt-6.2-astra", "effort": "high",
    }]), encoding="utf-8")

    spook.restart_service(1)

    assert UTTERANCE in read_page(page).transcript and "Про тесты." in read_page(page).summary
    assert page.name in index.read_text(encoding="utf-8")
    assert not snapshot.exists()
    assert len(spook.agent.calls()) == 1
    assert "Агент прерван перезапуском службы" in journal(spook, page)


def test_untrusted_output_and_stderr_cannot_copy_meeting_text_into_the_journal(spook):
    spook.agent.path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "meeting = json.load(sys.stdin)\n"
        "private = meeting['transcript'] + ' Слова из ответа Codex.'\n"
        "print(json.dumps({'type': private}))\n"
        "print(json.dumps({'type': 'turn.failed', 'error': {'message': private}}))\n"
        "print(private, file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    with spook.events() as events:
        finish_meeting(spook, events)
        events.wait_for("agent", containing="ошибка")

    [page] = spook.pages()
    saved = journal(spook, page)
    assert "turn.failed" in saved and "Код возврата: 1" in saved
    assert "Codex завершился с кодом 1" in saved
    assert UTTERANCE not in saved and "Слова из ответа Codex." not in saved
