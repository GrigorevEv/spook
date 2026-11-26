from __future__ import annotations

from conftest import speech


def note_names(spook) -> list[str]:
    return sorted(path.name for path in spook.meetings_dir.iterdir())


def test_topic_entered_before_start_is_in_note_name_at_once(spook):
    response = spook.start_meeting(topic="Репликация в Raft")

    assert response.status_code == 200, response.text
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert "Репликация в Raft" in spook.page().text


def test_characters_forbidden_in_note_names_are_dropped_from_topic(spook):
    spook.start_meeting(topic=' Raft:\t"репликация" <журнала>/логов? #1 *|\\ ')

    assert note_names(spook) == ["2026-11-06 14-02 Raft репликация журнала логов 1.md"]
    assert "Raft репликация журнала логов 1" in spook.page().text


def test_topic_of_spaces_and_forbidden_characters_only_counts_as_no_topic(spook):
    spook.start_meeting(topic=' \t ?*"\\ ')

    assert note_names(spook) == ["2026-11-06 14-02 Встреча.md"]
    assert 'name="topic" value=""' in spook.page().text


def test_long_topic_is_cut_to_80_characters(spook):
    spook.start_meeting(
        topic="Распределённые системы — консенсус, репликация журнала, снимки состояния, "
        "перевыборы лидера и членство в кластере"
    )

    assert note_names(spook) == [
        "2026-11-06 14-02 Распределённые системы — консенсус, репликация журнала, снимки состояния, перевы.md"
    ]


def finish_session(spook) -> None:
    spook.recognizer.say("Реплика.")
    spook.feed_system(speech(20))
    spook.stop()
    spook.wait_until(lambda: "нет Сессии" in spook.page().text)


def test_notes_started_in_the_same_minute_get_numbered_names(spook):
    for _ in range(3):
        assert spook.start_meeting().status_code == 200
        finish_session(spook)

    assert note_names(spook) == [
        "2026-11-06 14-02 Встреча (2).md",
        "2026-11-06 14-02 Встреча (3).md",
        "2026-11-06 14-02 Встреча.md",
    ]


def test_renaming_after_stop_does_not_overwrite_note_with_the_same_name(spook):
    spook.start_meeting(topic="Raft")
    finish_session(spook)
    spook.start_meeting()
    spook.set_topic("Raft")
    finish_session(spook)

    assert note_names(spook) == ["2026-11-06 14-02 Raft (2).md", "2026-11-06 14-02 Raft.md"]


def test_topic_is_editable_before_and_during_meeting(spook):
    html = spook.page().text
    start_form = html[html.index('hx-post="/session/start"') : html.index(">Старт</button>")]
    assert "Тема" in start_form and 'name="topic"' in start_form
    assert 'name="link"' not in start_form

    spook.start_meeting(topic="Репликация в Raft")
    html = spook.page().text

    assert 'name="topic"' in html and 'value="Репликация в Raft"' in html
    assert 'name="link"' not in html
    assert 'hx-post="/session/topic"' in html


def test_topic_without_session_is_rejected(spook):
    response = spook.set_topic("Raft")
    assert response.status_code == 409
    assert "Сессии нет" in response.text
    assert not spook.meetings_dir.exists()


def test_topic_entered_during_session_renames_note_only_after_stop(spook):
    spook.write_settings(auto_stop_minutes=60)
    spook.recognizer.say("Лидер рассылает записи журнала.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Лидер рассылает записи журнала.")

        response = spook.set_topic("Репликация в Raft")

        assert response.status_code == 200, response.text
        state = events.wait_for("state", containing="Репликация в Raft")
        assert "2026-11-06 14-02 Встреча.md" in state.data
        assert note_names(spook) == ["2026-11-06 14-02 Встреча.md"]

        spook.clock.advance(45 * 60)
        spook.stop()
        finished = events.wait_for("state", containing="нет Сессии")

    assert "2026-11-06 14-02 Репликация в Raft.md" in finished.data
    assert note_names(spook) == ["2026-11-06 14-02 Репликация в Raft.md"]
    assert (spook.meetings_dir / "2026-11-06 14-02 Репликация в Raft.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:47"\n'
        "tags: [встреча]\n"
        "---\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Лидер рассылает записи журнала.\n"
    )
