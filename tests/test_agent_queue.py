"""Очередь Агента на заглушке: после «Стоп» Заметка уходит Агенту, состояние Выжимки видно на странице."""

from __future__ import annotations

import html
import logging
import re

from conftest import speech

NOTE_NAME = "2026-11-06 14-02 Лекция"


def lecture_with_utterance(spook, *texts: str) -> None:
    """Лекция с Репликами: «Старт», речь по сценарию, конец очереди распознавания."""
    spook.recognizer.say(*texts)
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20 * len(texts)))
        events.wait_for("paragraph", containing=texts[-1])


def test_after_stop_agent_runs_once_with_skill_note_model_and_effort_in_wiki_root(spook):
    spook.write_settings(lecture={"skill": "spook-lecture", "model": "sonnet", "effort": "medium"})
    spook.agent.play("summary")
    lecture_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="готово")

    note_path = spook.lectures_dir / f"{NOTE_NAME}.md"
    [call] = spook.agent.calls()
    assert call["argv"][: call["argv"].index("--disallowedTools")] == [  # запреты — `test_agent_launch.py`
        "-p", f"/spook-lecture {note_path}",
        "--model", "sonnet",
        "--effort", "medium",
        "--output-format", "stream-json", "--verbose", "--include-hook-events",
        "--permission-mode", "default", "--permission-prompts", "none",
    ]
    assert call["cwd"] == str(spook.wiki_dir)


def test_note_with_topic_goes_to_agent_under_its_final_name(spook):
    spook.agent.play("summary")
    lecture_with_utterance(spook, "Реплика.")
    spook.set_topic("Репликация в Raft")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    renamed = spook.lectures_dir / "2026-11-06 14-02 Репликация в Raft.md"
    assert [call["argv"][1] for call in spook.agent.calls()] == [f"/spook-lecture {renamed}"]
    assert "2026-11-06 14-02 Репликация в Raft" in agent.data
    assert "## О чём" in renamed.read_text(encoding="utf-8")
    assert (spook.state_dir / "agent" / "lecture" / "2026-11-06 14-02 Репликация в Raft.log").exists()


def test_agent_that_wrote_summary_above_transcript_gives_done(spook):
    spook.agent.play("summary")
    lecture_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="готово")

    assert NOTE_NAME in agent.data
    assert (spook.lectures_dir / f"{NOTE_NAME}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:02"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Реплика.\n"
    )
    page = spook.page().text
    assert "готово" in page and "ошибка" not in page


def test_agent_that_failed_gives_error_with_its_message(spook):
    spook.agent.play("fail")
    lecture_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="ошибка")

    assert "Сценарий: Агент упал." in agent.data
    assert "Сценарий: Агент упал." in spook.page().text


def test_agent_that_finished_without_summary_gives_error(spook):
    spook.agent.play("nothing")
    lecture_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        agent = events.wait_for("agent", containing="ошибка")

    assert "Выжимки в Заметке нет" in agent.data


def test_second_session_waits_in_queue_while_agent_works_on_first_and_start_is_allowed(spook):
    spook.agent.play("wait")
    lecture_with_utterance(spook, "Первая лекция.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="работает")
        spook.clock.advance(60 * 60)

        assert spook.start_lecture().status_code == 200
        spook.recognizer.say("Вторая лекция.")
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Вторая лекция.")
        spook.stop()
        both = events.wait_for("agent", containing="в очереди")
        assert "2026-11-06 15-02 Лекция" in both.data and "работает" in both.data
        spook.wait_until(lambda: len(spook.agent.calls()) == 1)

        spook.agent.release()
        first_done = events.wait_for("agent", containing="готово")
        assert "работает" in first_done.data or "в очереди" in first_done.data
        spook.agent.release()
        while "работает" in (final := events.wait_for("agent")).data or "в очереди" in final.data:
            pass

    assert final.data.count("готово") == 2
    assert [call["argv"][1].split(" ", 1)[1] for call in spook.agent.calls()] == [
        str(spook.lectures_dir / "2026-11-06 14-02 Лекция.md"),
        str(spook.lectures_dir / "2026-11-06 15-02 Лекция.md"),
    ]


def test_agent_log_is_kept_in_state_dir_outside_vault_and_opens_from_page(spook):
    spook.agent.play("fail")
    lecture_with_utterance(spook, "Реплика.")

    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="ошибка")

    log_path = spook.state_dir / "agent" / "lecture" / f"{NOTE_NAME}.log"
    assert log_path.read_text(encoding="utf-8") == (
        f"$ {spook.agent.path} -p '/spook-lecture {spook.lectures_dir / NOTE_NAME}.md' --model opus --effort high"
        " --output-format stream-json --verbose --include-hook-events --permission-mode default --permission-prompts none"
        f" --disallowedTools 'Edit(/{spook.wiki_dir}/**/.claude/**)' 'Edit(/{spook.meetings_dir}/**/.claude/**)'"
        f" 'Edit(/{spook.lectures_dir}/**/.claude/**)' 'Edit(/{spook.wiki_dir}/_tools/**)' 'Edit(/{spook.wiki_dir}/_templates/**)'\n"
        f"(рабочий каталог: {spook.wiki_dir})\n"
        "\n"
        "Заглушка Агента: сценарий «fail».\n"
        "\n"
        "--- stderr ---\n"
        "Сценарий: Агент упал.\n"
        "\n"
        "Код возврата: 1. Выжимка: ошибка — Сценарий: Агент упал.\n"
    )
    assert sorted(path.name for path in spook.lectures_dir.iterdir()) == [f"{NOTE_NAME}.md"]

    page = spook.page().text
    link = re.search(r'href="(/agent/log\?note=[^"]+)"', page)
    assert link, page
    journal = spook.http.get(f"{spook.base_url}{link.group(1)}")
    assert journal.status_code == 200
    assert journal.headers["content-type"].startswith("text/plain")
    assert journal.text == log_path.read_text(encoding="utf-8")


def test_notification_reports_ready_summary(spook):
    spook.agent.play("summary")
    lecture_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())

    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", NOTE_NAME]]


def test_notification_reports_agent_error_with_message(spook):
    spook.agent.play("fail")
    lecture_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())

    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Ошибка Агента", f"{NOTE_NAME}: Сценарий: Агент упал."]
    ]


def test_service_journal_records_agent_start_and_outcome(spook, caplog):
    # Приёмка (задача 30): в журнале службы о работе Агента не было ни строки.
    caplog.set_level(logging.INFO, logger="spook")
    spook.write_settings(lecture={"model": "sonnet", "effort": "medium"})
    spook.agent.play("fail")
    lecture_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: spook.notify.calls())
    spook.wait_until(lambda: "Сценарий: Агент упал." in caplog.text)

    journal = [record.getMessage() for record in caplog.records if NOTE_NAME in record.getMessage()]
    assert journal == [
        f"Агент запущен: {NOTE_NAME} — spook-lecture, sonnet, medium",
        f"Агент закончил: {NOTE_NAME} — ошибка: Сценарий: Агент упал.",
    ]


def test_unsent_notification_is_recorded_in_service_journal(spook, caplog):
    spook.agent.play("summary")
    spook.notify.play("fail")
    lecture_with_utterance(spook, "Реплика.")

    spook.stop()
    spook.wait_until(lambda: "Уведомление" in caplog.text)

    [warning] = [record for record in caplog.records if record.getMessage().startswith("Уведомление")]
    assert warning.levelname == "WARNING"
    assert "«Выжимка готова»" in warning.getMessage()
    assert "служба уведомлений недоступна" in warning.getMessage()


def test_session_without_utterances_does_not_run_agent_and_leaves_no_empty_note(spook):
    spook.recognizer.say("")
    spook.start_lecture()
    assert (spook.lectures_dir / f"{NOTE_NAME}.md").exists()

    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert list(spook.lectures_dir.iterdir()) == []
    page = spook.page().text
    assert "Выжимка" not in page and "Заметка:" not in page
    assert spook.agent.calls() == [] and spook.notify.calls() == []


def test_agent_queue_survives_service_restart_interrupted_run_is_error_and_waiting_note_runs(spook):
    first = spook.lectures_dir / f"{NOTE_NAME}.md"
    second = spook.lectures_dir / "2026-11-06 15-02 Лекция.md"
    spook.agent.play("spoil-and-hang")  # Агент пишет Выжимку, портит Транскрипт и работает дальше
    lecture_with_utterance(spook, "Первая лекция.")
    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="работает")
    spook.wait_until(lambda: "Транскрипт переписан Агентом." in first.read_text(encoding="utf-8"))
    spook.clock.advance(60 * 60)
    lecture_with_utterance(spook, "Вторая лекция.")
    with spook.events() as events:
        spook.stop()
        events.wait_for("agent", containing="в очереди")
    spook.agent.play("summary")

    spook.restart_service(60)

    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Ошибка Агента", f"{NOTE_NAME}: Агент прерван перезапуском службы"],
        ["Выжимка готова", second.stem],
    ]
    assert [call["argv"][1] for call in spook.agent.calls()] == [f"/spook-lecture {first}", f"/spook-lecture {second}"]
    # Транскрипт прерванного запуска сверен по снимку, сделанному до запуска; Выжимка осталась.
    assert first.read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:02"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Первая лекция.\n"
    )
    page = spook.page().text
    assert "Агент прерван перезапуском службы" in page
    assert "Агент изменил Транскрипт — возвращён исходный." in page
    journal = spook.http.get(f"{spook.base_url}/agent/log", params={"note": NOTE_NAME}).text
    assert journal.endswith(
        "\nОхрана Транскрипта: Агент изменил Транскрипт — возвращён исходный.\n"
        "\nВыжимка: ошибка — Агент прерван перезапуском службы\n"
    )


def test_meeting_and_lecture_with_same_name_keep_separate_journals(spook):
    spook.agent.play("fail")
    spook.recognizer.say("Лекция.")
    spook.start_lecture(topic="Планёрка")
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Лекция.")
        spook.stop()
        events.wait_for("agent", containing="ошибка")
    spook.agent.play("summary")
    spook.recognizer.say("Встреча.")
    spook.start_meeting(topic="Планёрка")  # та же минута, та же Тема — то же имя в другой папке
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Встреча.")
        spook.stop()
        events.wait_for("agent", containing="готово")

    for page in (spook.page().text, spook.http.get(f"{spook.base_url}/notes").text):
        links = re.findall(r'href="(/agent/log\?note=[^"]+)"', page)
        assert len(links) == 2, page
        journals = sorted(spook.http.get(f"{spook.base_url}{html.unescape(link)}").text for link in links)
        meeting, lecture = sorted(journals, key=lambda journal: "/spook-lecture" in journal)
        assert "/spook-meeting" in meeting and "/spook-lecture" not in meeting
        assert "/spook-lecture" in lecture and "/spook-meeting" not in lecture
        assert meeting.endswith("Выжимка: готово\n") and lecture.endswith("Выжимка: ошибка — Сценарий: Агент упал.\n")


def test_log_of_unknown_note_is_not_found_and_paths_outside_log_dir_are_refused(spook):
    assert spook.http.get(f"{spook.base_url}/agent/log", params={"note": "нет такой"}).status_code == 404
    spook.state_dir.mkdir(parents=True, exist_ok=True)
    (spook.state_dir / "secret.log").write_text("не журнал", encoding="utf-8")

    response = spook.http.get(f"{spook.base_url}/agent/log", params={"note": "../secret"})

    assert response.status_code == 404
    assert "не журнал" not in response.text
