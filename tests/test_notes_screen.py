from __future__ import annotations

import json
import re
from html import escape, unescape
from pathlib import Path
from uuid import uuid4

from conftest import agent_model_and_effort
from spook.wisp import page_filename
from test_meeting_agent_context import wisp_article
from wisp_page import read_page


def write_note(
    folder: Path, date: str, start: str, end: str, topic: str = "", *,
    summary: str = "", transcript_html: str | None = None, filename: str | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    time = start.replace(":", "-")
    title = f"{date} {time} {topic or 'Встреча'}"
    path = folder / (filename or page_filename(title))
    properties = {"id": uuid4().hex, "date": date, "start": start, "end": end, "topic": topic}
    metadata = json.dumps({"title": title, "section": "Встречи", "spook": properties}, ensure_ascii=False)
    transcript = transcript_html if transcript_html is not None else f"<p><strong>{start} Собеседники:</strong> Реплика.</p>"
    path.write_text(
        f'<!doctype html>\n<title>{escape(title)}</title>\n<meta name="description" content="">\n'
        f'<script type="application/json" id="wiki-meta">{metadata}</script>\n'
        f'<h1>{escape(title)}</h1>\n<section class="card" id="выжимка"><h2>Выжимка</h2>\n{summary}\n</section>\n'
        f'<section class="card" id="транскрипт"><h2>Транскрипт</h2>\n{transcript}\n</section>\n',
        encoding="utf-8",
    )
    return path


def rows(page: str) -> list[str]:
    result = []
    for row in re.findall(r'<tr class="note".*?</tr>', page, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        texts = [" ".join(unescape(re.sub(r"<[^>]+>", " ", cell)).split()) for cell in cells]
        result.append(" | ".join(texts[:5]))
    return result


def notes_page(spook) -> str:
    response = spook.http.get(f"{spook.base_url}/notes")
    assert response.status_code == 200
    return response.text


def test_notes_screen_lists_meetings_newest_first(spook):
    write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про Raft.</p>")
    write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    write_note(spook.meetings_dir, "2026-11-04", "23:40", "00:10")
    (spook.meetings_dir / "Словарь терминов.md").write_text("Raft\n", encoding="utf-8")
    (spook.meetings_dir / "Планы на год.md").write_text("Планы.\n", encoding="utf-8")
    wisp_article(spook.meetings_dir / "2026-11-06-13-00-foreign.html", "Страница пользователя", "Встречи")
    wisp_article(spook.meetings_dir / "2026-11-06-12-00-plan.html", "План", "Проекты")

    assert rows(notes_page(spook)) == [
        "2026-11-06 14:02 | без Темы | Встреча | 45 мин | нет",
        "2026-11-05 10:00 | Репликация в Raft | Встреча | 1 ч 05 мин | готово",
        "2026-11-04 23:40 | без Темы | Встреча | 30 мин | нет",
    ]


def test_deleted_note_file_disappears_from_list(spook):
    write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft")
    deleted = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47", "Планёрка")
    assert len(rows(notes_page(spook))) == 2

    deleted.unlink()

    assert [row.split(" | ")[1] for row in rows(notes_page(spook))] == ["Репликация в Raft"]


def test_screens_link_to_each_other(spook):
    assert 'href="/notes"' in spook.page().text
    assert 'href="/"' in notes_page(spook)


def command(spook, action: str, note: Path | str, **fields: str):
    return spook.http.post(f"{spook.base_url}/notes/{action}", data={"note": str(note), **fields})


def test_open_in_wisp_passes_the_published_page_uri_to_the_opener(spook):
    note = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    assert 'hx-post="/notes/wisp"' in notes_page(spook)

    response = command(spook, "wisp", note)

    assert response.status_code == 200
    [call] = spook.opener.calls()
    [uri] = call["argv"]
    assert uri == note.as_uri()


def test_old_wiki_and_obsidian_commands_are_absent(spook):
    note = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    assert 'hx-post="/notes/wiki"' not in notes_page(spook)

    for action in ("wiki", "obsidian"):
        assert command(spook, action, note).status_code == 404
    assert spook.opener.calls() == []


def test_commands_accept_only_published_meetings_from_wisp(spook, tmp_path):
    valid = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    outside = write_note(tmp_path / "чужое", "2026-11-06", "14:02", "14:47", "Чужая")
    (spook.meetings_dir / "Словарь терминов.md").write_text("Raft\n", encoding="utf-8")
    (spook.meetings_dir / "2026-11-06-15-00-link.html").symlink_to(outside)
    link = spook.meetings_dir / "another-meeting.html"
    link.symlink_to(valid)
    foreign = spook.meetings_dir / "custom.html"
    wisp_article(foreign, "Своя страница", "Встречи")
    files = [valid, outside, link, foreign, spook.meetings_dir / "index.html"]
    before = {path: path.read_bytes() for path in files}
    refused = [
        link, foreign, spook.meetings_dir / "index.html", outside,
        spook.meetings_dir / ".." / "чужое" / outside.name,
        spook.meetings_dir / ".." / ".." / "чужое" / outside.name,
        spook.meetings_dir / "Словарь терминов.md",
        spook.meetings_dir / "2026-11-06-15-00-link.html",
        spook.meetings_dir / "2026-11-06-16-00-missing.html",
        "2026-11-06 14-02 Встреча.md",
        "",
    ]

    for note in refused:
        for action in ("wisp", "agent"):
            response = command(spook, action, note, model="gpt-6.2-astra", effort="high", confirm="yes")
            assert response.status_code == 409, (action, note)

    assert spook.opener.calls() == [] and spook.agent.calls() == []
    assert {path: path.read_bytes() for path in files} == before


def row_of(spook, name: str) -> str:
    [row] = [row for row in re.findall(r'<tr class="note".*?</tr>', notes_page(spook), flags=re.S) if name in row]
    return row


def test_restart_agent_warns_that_summary_will_be_rewritten_and_runs_with_chosen_model_and_effort(spook):
    spook.write_settings(meeting={"model": "gpt-6.2-astra", "effort": "medium"})
    note = write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про Raft.</p>")
    row = row_of(spook, note.stem)
    assert "Перезапустить Агента" in row
    assert "Выжимка будет написана заново" in row
    assert '<option value="gpt-6.2-astra" selected>' in row and '<option value="medium" selected>' in row
    assert '<input type="checkbox" name="confirm" value="yes" required>' in row
    spook.agent.play("summary")

    unconfirmed = command(spook, "agent", note, model="gpt-6.2-sol", effort="low")
    assert unconfirmed.status_code == 409
    assert "Выжимка будет написана заново" in unconfirmed.text
    assert spook.agent.calls() == []

    assert command(spook, "agent", note, model="gpt-6.2-sol", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0] and spook.notify.calls())

    [call] = spook.agent.calls()
    assert agent_model_and_effort(call) == ("gpt-6.2-sol", "low")
    assert call["input"]["topic"] == "Репликация в Raft"
    assert call["input"]["transcript"].strip() == "**10:00 Собеседники:** Реплика."


def test_restart_after_which_agent_left_old_summary_unchanged_gives_error_summary_not_updated(spook):
    note = write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про тесты.</p>")
    spook.agent.play("same-summary")

    assert command(spook, "agent", note, model="gpt-6.2-sol", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    assert "Агент завершился, но Выжимка не обновлена" in rows(notes_page(spook))[0]
    assert "Про тесты." in note.read_text(encoding="utf-8")


def test_after_restarts_session_screen_shows_note_once_with_last_run_state(spook):
    note = write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про Raft.</p>")
    spook.agent.play("summary")
    assert command(spook, "agent", note, model="gpt-6.2-sol", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    spook.agent.play("same-summary")
    assert command(spook, "agent", note, model="gpt-6.2-sol", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    agent_block = spook.page().text.split('<section id="agent"', 1)[1].split("</section>", 1)[0]

    assert re.findall(r'<p class="note-name">([^<]*)</p>', agent_block) == [read_page(note).title]
    assert "Агент завершился, но Выжимка не обновлена" in agent_block
    assert 'data-summary="done"' not in agent_block


def test_agent_log_after_restart_keeps_previous_run_before_new_one(spook):
    note = write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про Raft.</p>")
    spook.agent.play("summary")
    command(spook, "agent", note, model="gpt-6.2-astra", effort="medium", confirm="yes")
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    spook.agent.play("nothing")
    command(spook, "agent", note, model="gpt-6.2-sol", effort="low", confirm="yes")
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": note.stem}).text

    first, second = journal.index("--model gpt-6.2-astra"), journal.index("--model gpt-6.2-sol")
    assert first < journal.index("turn.completed") < journal.index("Выжимка: готово") < second
    assert second < journal.rindex("turn.completed") < journal.index("Выжимка: ошибка")


def test_note_without_summary_offers_agent_start_without_warning(spook):
    note = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    row = row_of(spook, note.stem)
    assert "Запустить Агента" in row and "Перезапустить Агента" not in row
    assert "Выжимка будет написана заново" not in row
    assert '<option value="gpt-6.2-astra" selected>' in row and '<option value="high" selected>' in row
    spook.agent.play("fail")

    assert command(spook, "agent", note, model="gpt-6.2-astra", effort="high").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    assert rows(notes_page(spook)) == ["2026-11-06 14:02 | без Темы | Встреча | 45 мин | ошибка Codex завершился с кодом 1"]
    [call] = spook.agent.calls()
    assert agent_model_and_effort(call) == ("gpt-6.2-astra", "high")
    assert call["input"]["topic"] == ""
    assert str(note) not in call["argv"]


def test_agent_log_opens_for_every_note_that_had_a_run(spook):
    spook.agent.play("summary")
    ran_now = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    ran_before = write_note(spook.meetings_dir, "2026-11-05", "10:00", "11:05", "Репликация в Raft", summary="<h2>Суть</h2>\n<p>Про Raft.</p>")
    never = write_note(spook.meetings_dir, "2026-11-04", "09:00", "09:30", "Планёрка", summary="<h2>Суть</h2>\n<p>Планы.</p>")
    old_version_log_without_kind = spook.state_dir / "agent" / f"{ran_before.stem}.log"
    old_version_log_without_kind.parent.mkdir(parents=True)
    old_version_log_without_kind.write_text("Журнал прошлой работы службы.\n", encoding="utf-8")
    lecture_log = spook.state_dir / "agent" / "lecture" / f"{ran_now.stem}.log"
    lecture_log.parent.mkdir()
    lecture_log.write_text("Старый Журнал Агента.\n", encoding="utf-8")
    command(spook, "agent", ran_now, model="gpt-6.2-astra", effort="high")
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])

    for note, log in (
        (ran_now, spook.state_dir / "agent" / "meeting" / f"{ran_now.stem}.log"),
        (ran_before, old_version_log_without_kind),
    ):
        link = re.search(r'href="(/agent/log\?note=[^"]+)"', row_of(spook, note.stem))
        assert link, note
        journal = spook.http.get(f"{spook.base_url}{unescape(link.group(1))}")
        assert journal.status_code == 200
        assert journal.text == log.read_text(encoding="utf-8")
    assert "/agent/log" not in row_of(spook, never.stem)
    assert lecture_log.read_text(encoding="utf-8") == "Старый Журнал Агента.\n"


def test_agent_is_not_started_twice_for_note_waiting_for_it(spook):
    spook.agent.play("wait")
    note = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")
    assert command(spook, "agent", note, model="gpt-6.2-astra", effort="high").status_code == 200
    spook.wait_until(lambda: len(spook.agent.calls()) == 1)
    row = row_of(spook, note.stem)
    assert "работает" in row and "Запустить Агента" not in row
    assert 'hx-get="/notes/row?note=' in row

    again = command(spook, "agent", note, model="gpt-6.2-astra", effort="high")

    assert again.status_code == 409 and "уже работает" in again.text
    spook.agent.release()
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    assert len(spook.agent.calls()) == 1
    polled = spook.http.get(f"{spook.base_url}/notes/row", params={"note": str(note)})
    assert polled.status_code == 200
    assert "готово" in polled.text and "hx-trigger" not in polled.text


def test_note_of_running_session_is_listed_without_agent_run(spook):
    spook.recognizer.say("Реплика.")
    spook.start_meeting()
    note = spook.current_note()

    row = row_of(spook, "Сессия идёт")
    assert "Сессия идёт" in row and "Запустить Агента" not in row
    response = command(spook, "agent", note, model="gpt-6.2-astra", effort="high")
    assert response.status_code == 409
    assert "Открыть в Wisp" not in row
    assert spook.agent.calls() == []


def test_agent_run_refuses_model_or_effort_that_is_not_a_plain_name(spook):
    note = write_note(spook.meetings_dir, "2026-11-06", "14:02", "14:47")

    for model, effort in (("--unsafe", "high"), ("gpt-6.2-astra", "high --verbose"), ("", "high")):
        assert command(spook, "agent", note, model=model, effort=effort).status_code == 409

    assert spook.agent.calls() == []
