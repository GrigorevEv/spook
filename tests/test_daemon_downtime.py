from __future__ import annotations

import json
from datetime import timedelta

from spook.audio import Source

from conftest import NOTE_AFTER_START, note_text, speech

NOTE_NAME = "2026-11-06 14-02 Встреча"


def session_record(spook) -> dict:
    return json.loads((spook.state_dir / "session.json").read_text(encoding="utf-8"))


def test_session_record_on_disk_has_no_sound_and_no_utterance_text(spook):
    spook.recognizer.say("Секретная Реплика.")
    spook.start_meeting(topic="Тесты")
    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Секретная Реплика.")
    spook.set_topic("Тесты и швы")
    spook.clock.advance(70)

    raw = (spook.state_dir / "session.json").read_text(encoding="utf-8")

    assert "Секретная" not in raw
    assert [path.name for path in spook.state_dir.iterdir() if path.is_file()] == ["session.json"]
    record = json.loads(raw)
    assert record["note"] == str(spook.meetings_dir / "2026-11-06 14-02 Тесты.md")
    assert record["kind"] == "meeting"
    assert record["topic"] == "Тесты и швы"
    assert record["state"] == "running"
    assert record["started_at"] == "2026-11-06T14:02:00+03:00"
    assert record["captured_seconds"] == 90
    assert record["last_active_at"] == "2026-11-06T14:03:30+03:00"
    assert set(record) <= {
        "note", "kind", "topic", "named_topic", "state", "started_at",
        "captured_seconds", "last_active_at", "paused_at", "last_speech_at",
    }


def test_session_record_is_removed_after_stop(spook):
    spook.recognizer.say("Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert not (spook.state_dir / "session.json").exists()


def test_short_downtime_continues_meeting_in_same_note_with_gap_mark_and_notification(spook):
    spook.recognizer.say("До перерыва.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До перерыва.")
    spook.clock.advance(40)

    spook.restart_service(2 * 60)
    spook.recognizer.say("После перерыва.")

    with spook.events() as events:
        state, transcript = events.next_event(), events.next_event()
        assert state.name == "state" and "идёт" in state.data
        assert transcript.name == "transcript"
        assert transcript.data.index("До перерыва.") < transcript.data.index("<em>Пропуск 14:03–14:05</em>")
        spook.feed_system(speech(20))
        after = events.wait_for("paragraph", containing="После перерыва.")
    assert "<time>14:05</time>" in after.data
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** До перерыва.\n"
        "\n*Пропуск 14:03–14:05*\n"
        "\n**14:05 Собеседники:** После перерыва.\n"
    )
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия продолжена", "Перерыв в работе Spook 14:03–14:05, в Заметке отметка о пропуске"]
    ]
    assert ">0:01:00</time>" in spook.page().text


def test_long_downtime_finishes_meeting_at_last_activity_renames_and_queues_agent(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Обсудили план.")
    spook.start_meeting()
    spook.set_topic("Планёрка")
    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Обсудили план.")
    spook.clock.advance(160)

    spook.restart_service(60 * 60)

    note = spook.meetings_dir / "2026-11-06 14-02 Планёрка.md"
    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:05–15:05 — без речи дольше 5 минут"],
        ["Выжимка готова", "2026-11-06 14-02 Планёрка"],
    ]
    assert [call["argv"][1] for call in spook.agent.calls()] == [f"/spook-meeting {note}"]
    assert note.read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-11-06\n"
        'start: "14:02"\n'
        'end: "14:05"\n'
        "tags: [встреча]\n"
        "---\n"
        "\n"
        "## Суть\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Обсудили план.\n"
    )
    assert not (spook.state_dir / "session.json").exists()
    page = spook.page().text
    assert "нет Сессии" in page and ">Старт</button>" in page


def test_short_downtime_on_pause_stays_paused_without_gap_mark(spook):
    spook.recognizer.say("До Паузы.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До Паузы.")
    spook.pause()
    spook.clock.advance(100)

    spook.restart_service(3 * 60)
    spook.recognizer.say("После Паузы.")

    page = spook.page().text
    assert "на Паузе" in page and ">Продолжить</button>" in page and ">0:00:20</time>" in page
    assert spook.audio.active_sources == set()
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия на Паузе", "Перерыв в работе Spook 14:04–14:07, Сессия осталась на Паузе"]
    ]
    with spook.events() as events:
        assert spook.resume().status_code == 200
        events.wait_for("mark", containing="Пауза")
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="После Паузы.")
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** До Паузы.\n"
        "\n*Пауза 14:02–14:07*\n"
        "\n**14:07 Собеседники:** После Паузы.\n"
    )


def test_short_downtime_in_meeting_marks_gap_by_clock_and_restarts_both_sources(spook):
    spook.recognizer.say("Начали.")
    spook.start_meeting()
    with spook.events() as events:
        spook.audio.feed(speech(20), Source.SYSTEM)
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Начали.")
    spook.clock.advance(40)

    spook.restart_service(2 * 60)
    spook.recognizer.say("Я снова здесь.")

    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}
    with spook.events() as events:
        spook.audio.feed(speech(20), Source.MIC)
        spook.audio.feed(speech(20), Source.SYSTEM)
        events.wait_for("paragraph", containing="Я снова здесь.")
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Начали.\n"
        "\n*Пропуск 14:03–14:05*\n"
        "\n**14:05 Я:** Я снова здесь.\n"
    )


def test_record_of_session_whose_note_is_gone_is_removed_silently(spook, caplog):
    spook.start_meeting()
    spook.clock.advance(60)
    (spook.meetings_dir / f"{NOTE_NAME}.md").unlink()

    spook.restart_service(60)

    assert not (spook.state_dir / "session.json").exists()
    assert "нет Сессии" in spook.page().text
    assert spook.notify.calls() == [] and spook.agent.calls() == []
    assert "больше нет" in caplog.text


def test_long_downtime_of_session_without_utterances_removes_empty_note_without_agent(spook):
    spook.start_meeting()
    spook.clock.advance(60)

    spook.restart_service(10 * 60)

    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:03–14:13 — без речи дольше 5 минут"]
    ]
    spook.wait_until(lambda: not (spook.state_dir / "session.json").exists())
    assert list(spook.meetings_dir.iterdir()) == [] and spook.agent.calls() == []


def test_short_sleep_without_restart_leaves_gap_mark_before_speech_after_waking(spook):
    spook.recognizer.say("До сна.", "Хвост.", "После сна.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(30))
        spook.clock.advance(30)
        events.wait_for("paragraph", containing="До сна.")

        spook.clock.sleep(3 * 60)
        spook.feed_system(speech(20))

        mark = events.wait_for("mark", containing="Пропуск")
        events.wait_for("paragraph", containing="После сна.")

    assert "<em>Пропуск 14:02–14:05</em>" in mark.data
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** До сна. Хвост.\n"
        "\n*Пропуск 14:02–14:05*\n"
        "\n**14:05 Собеседники:** После сна.\n"
    )
    assert "идёт" in spook.page().text
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия продолжена", "Перерыв в работе Spook 14:02–14:05, в Заметке отметка о пропуске"]
    ]


def test_long_sleep_without_restart_finishes_session_at_last_activity_not_at_waking(spook):
    spook.agent.play("summary")
    spook.recognizer.say("До сна.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="До сна.")
        spook.clock.advance(3 * 60)

        spook.clock.sleep(60 * 60)
        spook.clock.advance(30)

        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")

    assert 'end: "14:05"' in note_text(spook)
    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()][0] == [
        "Сессия завершена", "Перерыв в работе Spook 14:05–15:05 — без речи дольше 5 минут"
    ]
    assert not (spook.state_dir / "session.json").exists()


def test_clock_jump_caught_by_auto_stop_check_still_ends_session_at_last_activity(spook):
    spook.write_settings(auto_stop_minutes=1.25)
    spook.recognizer.say("До скачка.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="До скачка.")
        spook.clock.advance(70)

        spook.clock.set(spook.clock.now() + timedelta(minutes=10))

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:03"' in note_text(spook)


def test_downtime_while_finishing_completes_note_at_stop_time_and_queues_agent(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Успели распознать.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Успели распознать.")
    spook.recognizer.hold()
    spook.feed_system(speech(5))
    spook.clock.advance(100)
    spook.stop()

    spook.restart_service(60 * 60)

    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", NOTE_NAME]]
    assert 'end: "14:03"' in note_text(spook)
    assert not (spook.state_dir / "session.json").exists()


def test_silence_before_sleep_counts_together_with_sleep_and_finishes_session_at_last_activity(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Последняя Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
        spook.clock.advance(4 * 60 + 30)

        spook.clock.sleep(4 * 60 + 30)
        spook.feed_system(speech(20))

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:06"' in note_text(spook)
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()][0] == [
        "Сессия завершена", "Перерыв в работе Spook 14:06–14:11 — без речи дольше 5 минут"
    ]


def test_silence_before_restart_counts_together_with_downtime_and_finishes_session_at_last_activity(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Последняя Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
    spook.clock.advance(4 * 60 + 30)

    spook.restart_service(4 * 60 + 30)

    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:06–14:11 — без речи дольше 5 минут"],
        ["Выжимка готова", NOTE_NAME],
    ]
    assert 'end: "14:06"' in note_text(spook)


def test_after_short_downtime_silence_is_counted_from_last_utterance_not_from_return(spook):
    spook.recognizer.say("Последняя Реплика.")
    spook.start_meeting()
    with spook.events() as events:
        spook.feed_system(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
    spook.clock.advance(60)

    spook.restart_service(2 * 60)
    assert "идёт" in spook.page().text

    spook.clock.advance(2 * 60 - 1)
    assert "идёт" in spook.page().text
    spook.clock.advance(1)

    spook.wait_until(lambda: "нет Сессии" in spook.page().text)
    assert 'end: "14:07"' in note_text(spook)
    assert ["Сессия завершена", "5 минут без речи"] in [call["argv"] for call in spook.notify.calls()]
