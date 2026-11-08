"""Тема, Ссылка и имя Заметки — через HTTP-интерфейс службы, файлы Заметок и поток событий."""

from __future__ import annotations

from conftest import speech


def note_names(spook) -> list[str]:
    return sorted(path.name for path in spook.lectures_dir.iterdir())


def test_topic_entered_before_start_is_in_note_name_at_once(spook):
    response = spook.start_lecture(topic="Репликация в Raft")

    assert response.status_code == 200, response.text
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert "Репликация в Raft" in spook.page().text


def test_characters_forbidden_in_note_names_are_dropped_from_topic(spook):
    spook.start_lecture(topic=' Raft:\t"репликация" <журнала>/логов? #1 *|\\ ')

    assert note_names(spook) == ["2026-11-06 14-02 Raft репликация журнала логов 1.md"]
    assert "Raft репликация журнала логов 1" in spook.page().text


def test_topic_of_spaces_and_forbidden_characters_only_counts_as_no_topic(spook):
    spook.start_lecture(topic=' \t ?*"\\ ')

    assert note_names(spook) == ["2026-11-06 14-02 Лекция.md"]
    assert 'class="topic"' not in spook.page().text


def test_long_topic_is_cut_to_80_characters(spook):
    spook.start_lecture(
        topic="Распределённые системы — консенсус, репликация журнала, снимки состояния, "
        "перевыборы лидера и членство в кластере"
    )

    assert note_names(spook) == [
        "2026-11-06 14-02 Распределённые системы — консенсус, репликация журнала, снимки состояния, перевы.md"
    ]


def finish_session(spook) -> None:
    """«Стоп» после одной Реплики: пустую Заметку Сессия без Реплик удалила бы."""
    spook.recognizer.say("Реплика.")
    spook.audio.feed(speech(20))
    spook.stop()
    spook.wait_until(lambda: "нет Сессии" in spook.page().text)


def test_notes_started_in_the_same_minute_get_numbered_names(spook):
    for _ in range(3):
        assert spook.start_lecture().status_code == 200
        finish_session(spook)

    assert note_names(spook) == [
        "2026-11-06 14-02 Лекция (2).md",
        "2026-11-06 14-02 Лекция (3).md",
        "2026-11-06 14-02 Лекция.md",
    ]


def test_renaming_after_stop_does_not_overwrite_note_with_the_same_name(spook):
    spook.start_lecture(topic="Raft")
    finish_session(spook)
    spook.start_lecture()
    spook.set_topic("Raft")
    finish_session(spook)

    assert note_names(spook) == ["2026-11-06 14-02 Raft (2).md", "2026-11-06 14-02 Raft.md"]


def test_lecture_link_is_written_to_source_property(spook):
    spook.start_lecture(link="https://www.youtube.com/watch?v=64Zp3tzNbpE")

    assert (spook.lectures_dir / "2026-11-06 14-02 Лекция.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        "tags: [лекция]\n"
        'source: "https://www.youtube.com/watch?v=64Zp3tzNbpE"\n'
        "---\n"
        "\n"
        "## Транскрипт\n"
    )


def test_link_changed_during_session_updates_source_property_and_empty_link_removes_it(spook):
    spook.recognizer.say("Введение.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Введение.")
    note = spook.lectures_dir / "2026-11-06 14-02 Лекция.md"

    response = spook.set_link("https://example.org/talk")

    assert response.status_code == 200, response.text
    assert note.read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        "tags: [лекция]\n"
        'source: "https://example.org/talk"\n'
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Введение.\n"
    )

    assert spook.set_link("").status_code == 200
    assert "source" not in note.read_text(encoding="utf-8")


def test_page_has_topic_and_link_fields_in_start_form_and_keeps_them_editable_during_session(spook):
    html = spook.page().text
    start_form = html[html.index('hx-post="/session/start"') : html.index(">Старт</button>")]
    assert "Тема" in start_form and 'name="topic"' in start_form
    assert "Ссылка" in start_form and 'name="link"' in start_form

    spook.start_lecture(topic="Репликация в Raft", link="https://example.org/talk")
    html = spook.page().text

    assert 'name="topic"' in html and 'value="Репликация в Raft"' in html
    assert 'name="link"' in html and 'value="https://example.org/talk"' in html
    assert 'hx-post="/session/topic"' in html and 'hx-post="/session/link"' in html


def test_topic_and_link_commands_without_session_are_rejected_with_reason(spook):
    for response in (spook.set_topic("Raft"), spook.set_link("https://example.org/talk")):
        assert response.status_code == 409
        assert "Сессии нет" in response.text
    assert not spook.lectures_dir.exists()


def test_topic_entered_during_session_renames_note_only_after_stop(spook):
    spook.write_settings(auto_stop_minutes=60)  # Лекция молчит 45 минут до «Стоп»: автостоп не должен успеть
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")

        response = spook.set_topic("Репликация в Raft")

        assert response.status_code == 200, response.text
        state = events.wait_for("state", containing="Репликация в Raft")
        assert "2026-11-06 14-02 Лекция.md" in state.data
        assert note_names(spook) == ["2026-11-06 14-02 Лекция.md"]

        spook.clock.advance(45 * 60)
        spook.stop()
        finished = events.wait_for("state", containing="нет Сессии")

    assert "2026-11-06 14-02 Репликация в Raft.md" in finished.data
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert (spook.lectures_dir / "2026-11-06 14-02 Репликация в Raft.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:47"\n'
        "tags: [лекция]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**0:00:00** Лидер рассылает записи журнала.\n"
    )
