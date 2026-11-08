"""Экран «Заметки»: список Сессий по файлам папок видов, открытие в Obsidian и Вики, перезапуск Агента."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote


def write_note(folder: Path, name: str, date: str, start: str, end: str | None, tag: str, summary: str = "") -> Path:
    """Заметка так, как её оставляют приложение и Агент: свойства, Выжимка, Транскрипт."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    end_line = f'end: "{end}"\n' if end else ""
    above = f"{summary}\n\n" if summary else ""
    path.write_text(
        f'---\ndate: {date}\nstart: "{start}"\n{end_line}tags: [{tag}]\n---\n\n{above}## Транскрипт\n\n**0:00:00** Реплика.\n',
        encoding="utf-8",
    )
    return path


def rows(page: str) -> list[str]:
    """Строки списка Сессий текстом: ячейки через « | », без разметки."""
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


def test_notes_screen_lists_sessions_from_both_kind_folders_newest_first(spook):
    write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    write_note(spook.meetings_dir, "2026-11-06 14-02 Встреча", "2026-11-06", "14:02", "14:47", "встреча")
    write_note(spook.lectures_dir, "2026-11-04 23-40 Лекция", "2026-11-04", "23:40", "00:10", "лекция")
    (spook.meetings_dir / "Словарь терминов.md").write_text("Raft\n", encoding="utf-8")
    (spook.meetings_dir / "Планы на год.md").write_text("Планы.\n", encoding="utf-8")

    assert rows(notes_page(spook)) == [
        "2026-11-06 14:02 | без Темы | Встреча | 45 мин | нет",
        "2026-11-05 10:00 | Репликация в Raft | Лекция | 1 ч 05 мин | готово",
        "2026-11-04 23:40 | без Темы | Лекция | 30 мин | нет",
    ]


def test_deleted_note_file_disappears_from_list(spook):
    write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция")
    deleted = write_note(spook.meetings_dir, "2026-11-06 14-02 Планёрка", "2026-11-06", "14:02", "14:47", "встреча")
    assert len(rows(notes_page(spook))) == 2

    deleted.unlink()

    assert [row.split(" | ")[1] for row in rows(notes_page(spook))] == ["Репликация в Raft"]


def test_screens_link_to_each_other(spook):
    assert 'href="/notes"' in spook.page().text
    assert 'href="/"' in notes_page(spook)


def command(spook, action: str, note: Path | str, **fields: str):
    """Команда экрана «Заметки» так, как её отправляет форма строки."""
    return spook.http.post(f"{spook.base_url}/notes/{action}", data={"note": str(note), **fields})


def test_open_in_obsidian_calls_opener_with_obsidian_uri_of_absolute_path(spook):
    note = write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")
    assert 'hx-post="/notes/obsidian"' in notes_page(spook)

    response = command(spook, "obsidian", note)

    assert response.status_code == 200
    [call] = spook.opener.calls()
    [uri] = call["argv"]
    assert uri.startswith("obsidian://open?path=%2F")
    assert uri.endswith("%2Flectures%2F2026-11-06%2014-02%20%D0%9B%D0%B5%D0%BA%D1%86%D0%B8%D1%8F.md")
    assert unquote(uri.removeprefix("obsidian://open?path=")) == str(note)


def test_open_wiki_page_asks_wiki_tool_for_page_and_opens_its_file_uri(spook):
    note = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция")
    page = notes_page(spook)
    assert page.count('hx-post="/notes/wiki"') == 1

    response = command(spook, "wiki", note)

    assert response.status_code == 200
    assert [call["argv"] for call in spook.wiki.calls()] == [["file", "2026-11-05 10-00 Репликация в Raft"]]
    assert [call["argv"] for call in spook.opener.calls()] == [[f"file://{spook.wiki.control}/html/stranitsa-lektsii.html"]]


def test_missing_wiki_page_gives_reason_and_opens_nothing(spook):
    note = write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")
    spook.wiki.play("missing")

    response = command(spook, "wiki", note)

    assert response.status_code == 409
    assert "нет страницы" in response.text
    assert spook.opener.calls() == []


def test_meeting_has_no_wiki_page(spook):
    note = write_note(spook.meetings_dir, "2026-11-06 14-02 Встреча", "2026-11-06", "14:02", "14:47", "встреча")
    assert 'hx-post="/notes/wiki"' not in notes_page(spook)

    response = command(spook, "wiki", note)

    assert response.status_code == 409
    assert spook.wiki.calls() == [] and spook.opener.calls() == []


def test_commands_accept_only_session_notes_from_kind_folders(spook, tmp_path):
    write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")
    outside = write_note(tmp_path / "чужое", "2026-11-06 14-02 Чужая", "2026-11-06", "14:02", "14:47", "лекция")
    spook.meetings_dir.mkdir(parents=True)
    (spook.meetings_dir / "Словарь терминов.md").write_text("Raft\n", encoding="utf-8")
    (spook.lectures_dir / "2026-11-06 15-00 Ссылка наружу.md").symlink_to(outside)
    refused = [
        outside,
        spook.lectures_dir / ".." / "чужое" / outside.name,
        spook.lectures_dir / ".." / ".." / "чужое" / outside.name,
        spook.meetings_dir / "Словарь терминов.md",
        spook.lectures_dir / "2026-11-06 15-00 Ссылка наружу.md",
        spook.lectures_dir / "2026-11-06 16-00 Нет такой.md",
        "2026-11-06 14-02 Лекция.md",
        "",
    ]

    for note in refused:
        for action in ("obsidian", "wiki", "agent"):
            response = command(spook, action, note, model="opus", effort="high", confirm="yes")
            assert response.status_code == 409, (action, note)

    assert spook.opener.calls() == [] and spook.wiki.calls() == [] and spook.agent.calls() == []


def row_of(spook, name: str) -> str:
    """Разметка строки Сессии по имени Заметки."""
    [row] = [row for row in re.findall(r'<tr class="note".*?</tr>', notes_page(spook), flags=re.S) if name in row]
    return row


def test_restart_agent_warns_that_summary_will_be_rewritten_and_runs_with_chosen_model_and_effort(spook):
    spook.write_settings(lecture={"model": "sonnet", "effort": "medium"})
    note = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    row = row_of(spook, note.stem)
    assert "Перезапустить Агента" in row
    assert "Выжимка будет написана заново" in row
    assert '<option value="sonnet" selected>' in row and '<option value="medium" selected>' in row
    assert '<input type="checkbox" name="confirm" value="yes" required>' in row
    spook.agent.play("summary")

    unconfirmed = command(spook, "agent", note, model="haiku", effort="low")
    assert unconfirmed.status_code == 409
    assert "Выжимка будет написана заново" in unconfirmed.text
    assert spook.agent.calls() == []

    assert command(spook, "agent", note, model="haiku", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0] and spook.notify.calls())

    [call] = spook.agent.calls()
    assert call["argv"][: call["argv"].index("--disallowedTools")] == [  # запреты — `test_agent_launch.py`
        "-p", f"/spook-lecture {note}", "--model", "haiku", "--effort", "low",
        "--output-format", "stream-json", "--verbose", "--include-hook-events",
        "--permission-mode", "default", "--permission-prompts", "none",
    ]
    assert call["cwd"] == str(spook.wiki_dir)


def test_restart_after_which_agent_left_old_summary_unchanged_gives_error_summary_not_updated(spook):
    note = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    spook.agent.play("nothing")

    assert command(spook, "agent", note, model="haiku", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    assert "Агент завершился, но Выжимка не обновлена" in rows(notes_page(spook))[0]
    assert "Про Raft." in note.read_text(encoding="utf-8")


def test_after_restarts_session_screen_shows_note_once_with_last_run_state(spook):
    # Приёмка (задача 30): после двух перезапусков блок Агента на экране «Сессия» показывал одну
    # Заметку трижды — «готово», «ошибка», «готово» — и все три ссылки «Журнал» вели в один журнал.
    note = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    spook.agent.play("summary")
    assert command(spook, "agent", note, model="haiku", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    spook.agent.play("nothing")
    assert command(spook, "agent", note, model="haiku", effort="low", confirm="yes").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    agent_block = spook.page().text.split('<section id="agent"', 1)[1].split("</section>", 1)[0]

    assert re.findall(r'<p class="note-name">([^<]*)</p>', agent_block) == [note.stem]
    assert "Агент завершился, но Выжимка не обновлена" in agent_block
    assert 'data-summary="done"' not in agent_block


def test_agent_log_after_restart_keeps_previous_run_before_new_one(spook):
    # Приёмка (задача 30): журнал перезапуска затирал журнал прежнего запуска той же Заметки.
    note = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    spook.agent.play("summary")
    command(spook, "agent", note, model="sonnet", effort="medium", confirm="yes")
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    spook.agent.play("nothing")
    command(spook, "agent", note, model="haiku", effort="low", confirm="yes")
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": note.stem}).text

    first, second = journal.index("--model sonnet"), journal.index("--model haiku")
    assert first < journal.index("сценарий «summary»") < journal.index("Выжимка: готово") < second
    assert second < journal.index("сценарий «nothing»") < journal.index("Выжимка: ошибка")


def test_note_without_summary_offers_agent_start_without_warning(spook):
    note = write_note(spook.meetings_dir, "2026-11-06 14-02 Встреча", "2026-11-06", "14:02", "14:47", "встреча")
    row = row_of(spook, note.stem)
    assert "Запустить Агента" in row and "Перезапустить Агента" not in row
    assert "Выжимка будет написана заново" not in row
    assert '<option value="opus" selected>' in row and '<option value="high" selected>' in row
    spook.agent.play("fail")

    assert command(spook, "agent", note, model="opus", effort="high").status_code == 200
    spook.wait_until(lambda: "ошибка" in rows(notes_page(spook))[0])

    assert rows(notes_page(spook)) == ["2026-11-06 14:02 | без Темы | Встреча | 45 мин | ошибка Сценарий: Агент упал."]
    [call] = spook.agent.calls()
    assert call["argv"][:14] == [
        "-p", f"/spook-meeting {note}", "--model", "opus", "--effort", "high",
        "--output-format", "stream-json", "--verbose", "--include-hook-events",
        "--permission-mode", "default", "--permission-prompts", "none",
    ]
    assert "--append-system-prompt-file" in call["argv"]  # названия заметок для ссылок
    assert call["cwd"] == str(spook.meetings_dir)


def test_agent_log_opens_for_every_note_that_had_a_run(spook):
    spook.agent.play("summary")
    ran_now = write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")
    ran_before = write_note(spook.lectures_dir, "2026-11-05 10-00 Репликация в Raft", "2026-11-05", "10:00", "11:05", "лекция", "## О чём\n\nПро Raft.")
    never = write_note(spook.meetings_dir, "2026-11-04 09-00 Планёрка", "2026-11-04", "09:00", "09:30", "встреча", "## Суть\n\nПланы.")
    # Журнал прошлой работы службы — ещё без вида в пути, как его писали прежние версии.
    (spook.state_dir / "agent").mkdir(parents=True)
    (spook.state_dir / "agent" / f"{ran_before.stem}.log").write_text("Журнал прошлой работы службы.\n", encoding="utf-8")
    command(spook, "agent", ran_now, model="opus", effort="high")
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])

    for note, log in (
        (ran_now, spook.state_dir / "agent" / "lecture" / f"{ran_now.stem}.log"),
        (ran_before, spook.state_dir / "agent" / f"{ran_before.stem}.log"),
    ):
        link = re.search(r'href="(/agent/log\?note=[^"]+)"', row_of(spook, note.stem))
        assert link, note
        journal = spook.http.get(f"{spook.base_url}{unescape(link.group(1))}")
        assert journal.status_code == 200
        assert journal.text == log.read_text(encoding="utf-8")
    assert "/agent/log" not in row_of(spook, never.stem)


def test_agent_is_not_started_twice_for_note_waiting_for_it(spook):
    spook.agent.play("wait")
    note = write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")
    assert command(spook, "agent", note, model="opus", effort="high").status_code == 200
    spook.wait_until(lambda: len(spook.agent.calls()) == 1)
    row = row_of(spook, note.stem)
    assert "работает" in row and "Запустить Агента" not in row
    assert 'hx-get="/notes/row?note=' in row

    again = command(spook, "agent", note, model="opus", effort="high")

    assert again.status_code == 409 and "уже работает" in again.text
    spook.agent.release()
    spook.wait_until(lambda: "готово" in rows(notes_page(spook))[0])
    assert len(spook.agent.calls()) == 1
    polled = spook.http.get(f"{spook.base_url}/notes/row", params={"note": str(note)})
    assert polled.status_code == 200
    assert "готово" in polled.text and "hx-trigger" not in polled.text


def test_note_of_running_session_is_listed_without_agent_run(spook):
    spook.recognizer.say("Реплика.")
    spook.start_lecture()
    note = spook.lectures_dir / "2026-11-06 14-02 Лекция.md"

    row = row_of(spook, note.stem)
    assert "Сессия идёт" in row and "Запустить Агента" not in row
    response = command(spook, "agent", note, model="opus", effort="high")
    assert response.status_code == 409 and "Сессия с этой Заметкой ещё идёт" in response.text
    assert spook.agent.calls() == []


def test_agent_run_refuses_model_or_effort_that_is_not_a_plain_name(spook):
    note = write_note(spook.lectures_dir, "2026-11-06 14-02 Лекция", "2026-11-06", "14:02", "14:47", "лекция")

    for model, effort in (("--dangerously-skip-permissions", "high"), ("opus", "high --verbose"), ("", "high")):
        assert command(spook, "agent", note, model=model, effort=effort).status_code == 409

    assert spook.agent.calls() == []
