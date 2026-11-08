"""Перерыв в работе службы — главный шов: HTTP, поток событий, Заметка, заглушки Агента и уведомлений,
управляемые часы и перезапуск службы над тем же каталогом состояния.

Сведения об идущей Сессии служба держит в `<каталог состояния>/session.json`.
"""

from __future__ import annotations

import json
from datetime import timedelta

from spook.audio import Source

from conftest import MEETING_NOTE_AFTER_START, NOTE_AFTER_START, meeting_note_text, note_text, speech

NOTE_NAME = "2026-11-06 14-02 Лекция"


def session_record(spook) -> dict:
    return json.loads((spook.state_dir / "session.json").read_text(encoding="utf-8"))


def test_session_record_on_disk_has_no_sound_and_no_utterance_text(spook):
    spook.recognizer.say("Секретная Реплика.")
    spook.start_lecture(topic="Тесты", link="https://example.org/talk")
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Секретная Реплика.")
    spook.set_topic("Тесты и швы")
    spook.clock.advance(70)

    raw = (spook.state_dir / "session.json").read_text(encoding="utf-8")

    assert "Секретная" not in raw
    assert [path.name for path in spook.state_dir.iterdir() if path.is_file()] == ["session.json"]
    record = json.loads(raw)
    assert record["note"] == str(spook.lectures_dir / "2026-11-06 14-02 Тесты.md")
    assert record["kind"] == "lecture"
    assert record["topic"] == "Тесты и швы"
    assert record["link"] == "https://example.org/talk"
    assert record["state"] == "running"
    assert record["started_at"] == "2026-11-06T14:02:00+03:00"
    assert record["captured_seconds"] == 90
    assert record["last_active_at"] == "2026-11-06T14:03:30+03:00"  # обновляется, пока Сессия идёт
    assert set(record) <= {
        "note", "kind", "topic", "named_topic", "link", "state", "started_at",
        "captured_seconds", "last_active_at", "paused_at", "last_speech_at",
    }


def test_session_record_is_removed_after_stop(spook):
    spook.recognizer.say("Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert not (spook.state_dir / "session.json").exists()


def test_short_downtime_continues_lecture_in_same_note_with_gap_mark_and_notification(spook):
    spook.recognizer.say("До перерыва.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До перерыва.")
    spook.clock.advance(40)  # служба жива до 14:03, захвачена минута

    spook.restart_service(2 * 60)  # служба вернулась в 14:05
    spook.recognizer.say("После перерыва.")

    with spook.events() as events:
        state, transcript = events.next_event(), events.next_event()
        assert state.name == "state" and "идёт" in state.data
        assert transcript.name == "transcript"
        assert transcript.data.index("До перерыва.") < transcript.data.index("<em>Пропуск 0:01:00–0:03:00</em>")
        spook.audio.feed(speech(20))  # захват запущен заново
        after = events.wait_for("paragraph", containing="После перерыва.")
    assert "0:01:00" in after.data  # время от начала Лекции без учёта перерыва
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** До перерыва.\n"
        "\n*Пропуск 0:01:00–0:03:00*\n"
        "\n**0:01:00** После перерыва.\n"
    )
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия продолжена", "Перерыв в работе Spook 14:03–14:05, в Заметке отметка о пропуске"]
    ]
    assert ">0:01:00</time>" in spook.page().text  # таймер — захваченное время, без перерыва


def test_long_downtime_finishes_meeting_at_last_activity_renames_and_queues_agent(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Обсудили план.")
    spook.start_meeting()
    spook.set_topic("Планёрка")
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Обсудили план.")
    spook.clock.advance(160)  # служба жива до 14:05

    spook.restart_service(60 * 60)  # служба вернулась в 15:05

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
        "## О чём\n"
        "\n"
        "Про тесты.\n"
        "\n"
        "## Транскрипт\n"
        "\n**14:02 Собеседники:** Обсудили план.\n"
    )
    assert not (spook.state_dir / "session.json").exists()
    page = spook.page().text
    assert "нет Сессии" in page and ">Старт</button>" in page


def test_short_downtime_on_pause_keeps_session_on_pause_without_capture(spook):
    spook.recognizer.say("До Паузы.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="До Паузы.")
    spook.pause()  # 14:02:20, захвачено 20 секунд
    spook.clock.advance(100)  # служба жива до 14:04

    spook.restart_service(3 * 60)  # служба вернулась в 14:07
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
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="После Паузы.")
    # Пауза покрывает и перерыв: отдельной отметки о пропуске на Паузе нет.
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** До Паузы.\n"
        "\n*Пауза 0:00:20–0:05:00*\n"
        "\n**0:00:20** После Паузы.\n"
    )


def test_short_downtime_in_meeting_marks_gap_by_clock_and_restarts_both_sources(spook):
    spook.recognizer.say("Начали.")
    spook.start_meeting()
    with spook.events() as events:
        spook.audio.feed(speech(20), Source.SYSTEM)
        spook.clock.advance(20)
        events.wait_for("paragraph", containing="Начали.")
    spook.clock.advance(40)  # служба жива до 14:03

    spook.restart_service(2 * 60)  # служба вернулась в 14:05
    spook.recognizer.say("Я снова здесь.")

    assert spook.audio.active_sources == {Source.SYSTEM, Source.MIC}
    with spook.events() as events:
        spook.audio.feed(speech(20), Source.MIC)
        spook.audio.feed(speech(20), Source.SYSTEM)
        events.wait_for("paragraph", containing="Я снова здесь.")
    assert meeting_note_text(spook) == MEETING_NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Начали.\n"
        "\n*Пропуск 14:03–14:05*\n"
        "\n**14:05 Я:** Я снова здесь.\n"
    )


def test_record_of_session_whose_note_is_gone_is_removed_silently(spook, caplog):
    spook.start_lecture()
    spook.clock.advance(60)
    (spook.lectures_dir / f"{NOTE_NAME}.md").unlink()

    spook.restart_service(60)

    assert not (spook.state_dir / "session.json").exists()
    assert "нет Сессии" in spook.page().text
    assert spook.notify.calls() == [] and spook.agent.calls() == []
    assert "больше нет" in caplog.text  # запись в журнале службы


def test_long_downtime_of_session_without_utterances_removes_empty_note_without_agent(spook):
    spook.start_lecture()
    spook.clock.advance(60)

    spook.restart_service(10 * 60)

    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:03–14:13 — без речи дольше 5 минут"]
    ]
    spook.wait_until(lambda: not (spook.state_dir / "session.json").exists())
    assert list(spook.lectures_dir.iterdir()) == [] and spook.agent.calls() == []


def test_short_sleep_without_restart_leaves_gap_mark_before_speech_after_waking(spook):
    spook.recognizer.say("До сна.", "Хвост.", "После сна.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(30))
        spook.clock.advance(30)  # служба жива до 14:02:30, захвачено 30 секунд
        events.wait_for("paragraph", containing="До сна.")

        spook.clock.sleep(3 * 60)  # проснулись в 14:05:30, таймеры службы ещё не сработали
        spook.audio.feed(speech(20))

        mark = events.wait_for("mark", containing="Пропуск")
        events.wait_for("paragraph", containing="После сна.")

    assert "<em>Пропуск 0:00:30–0:03:30</em>" in mark.data
    # Недорезанный до сна звук распознан и записан до отметки; после неё — новый абзац.
    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** До сна. Хвост.\n"
        "\n*Пропуск 0:00:30–0:03:30*\n"
        "\n**0:00:30** После сна.\n"
    )
    assert "идёт" in spook.page().text
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия продолжена", "Перерыв в работе Spook 14:02–14:05, в Заметке отметка о пропуске"]
    ]


def test_long_sleep_without_restart_finishes_session_at_last_activity_not_at_waking(spook):
    spook.agent.play("summary")
    spook.recognizer.say("До сна.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="До сна.")
        spook.clock.advance(3 * 60)  # служба жива до 14:05

        spook.clock.sleep(60 * 60)
        spook.clock.advance(30)  # первая же проверка после пробуждения видит перерыв

        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")

    assert 'end: "14:05"' in note_text(spook)
    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()][0] == [
        "Сессия завершена", "Перерыв в работе Spook 14:05–15:05 — без речи дольше 5 минут"
    ]
    assert not (spook.state_dir / "session.json").exists()


def test_clock_jump_caught_by_auto_stop_check_still_ends_session_at_last_activity(spook):
    spook.write_settings(auto_stop_minutes=1.25)  # проверка автостопа в 14:03:15 — раньше следующей отметки активности
    spook.recognizer.say("До скачка.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="До скачка.")
        spook.clock.advance(70)  # служба жива до 14:03

        spook.clock.set(spook.clock.now() + timedelta(minutes=10))  # часы прыгнули, опоздавшие таймеры — сразу

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:03"' in note_text(spook)


def test_downtime_while_finishing_completes_note_at_stop_time_and_queues_agent(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Успели распознать.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Успели распознать.")
    spook.recognizer.hold()  # очередь распознавания не дорабатывается
    spook.audio.feed(speech(5))
    spook.clock.advance(100)
    spook.stop()  # 14:03:40 — «завершается», и тут служба обрывается

    spook.restart_service(60 * 60)  # звук, ждавший распознавания, пропал вместе с прежней службой

    spook.wait_until(lambda: spook.notify.calls())
    # Завершение после «Стоп» — без уведомления о перерыве; уведомляет только Агент.
    assert [call["argv"] for call in spook.notify.calls()] == [["Выжимка готова", NOTE_NAME]]
    assert 'end: "14:03"' in note_text(spook)
    assert not (spook.state_dir / "session.json").exists()


def test_silence_before_sleep_counts_together_with_sleep_and_finishes_session_at_last_activity(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Последняя Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
        spook.clock.advance(4 * 60 + 30)  # 4,5 минуты тишины, служба жива до 14:06:30

        spook.clock.sleep(4 * 60 + 30)  # 4,5 минуты сна: сам перерыв короче автостопа
        spook.audio.feed(speech(20))  # звук после пробуждения — первым видит перерыв

        events.wait_for("state", containing="нет Сессии")

    assert 'end: "14:06"' in note_text(spook)
    spook.wait_until(lambda: spook.notify.calls())
    assert [call["argv"] for call in spook.notify.calls()][0] == [
        "Сессия завершена", "Перерыв в работе Spook 14:06–14:11 — без речи дольше 5 минут"
    ]


def test_silence_before_restart_counts_together_with_downtime_and_finishes_session_at_last_activity(spook):
    spook.agent.play("summary")
    spook.recognizer.say("Последняя Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
    spook.clock.advance(4 * 60 + 30)  # 4,5 минуты тишины, служба жива до 14:06:30

    spook.restart_service(4 * 60 + 30)  # служба вернулась в 14:11

    spook.wait_until(lambda: len(spook.notify.calls()) == 2)
    assert [call["argv"] for call in spook.notify.calls()] == [
        ["Сессия завершена", "Перерыв в работе Spook 14:06–14:11 — без речи дольше 5 минут"],
        ["Выжимка готова", NOTE_NAME],
    ]
    assert 'end: "14:06"' in note_text(spook)


def test_after_short_downtime_silence_is_counted_from_last_utterance_not_from_return(spook):
    spook.recognizer.say("Последняя Реплика.")
    spook.start_lecture()
    with spook.events() as events:
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Последняя Реплика.")
    spook.clock.advance(60)  # служба жива до 14:03

    spook.restart_service(2 * 60)  # служба вернулась в 14:05, тишина — 3 минуты
    assert "идёт" in spook.page().text

    spook.clock.advance(2 * 60 - 1)
    assert "идёт" in spook.page().text
    spook.clock.advance(1)  # пять минут от последней Реплики

    spook.wait_until(lambda: "нет Сессии" in spook.page().text)
    assert 'end: "14:07"' in note_text(spook)
    assert ["Сессия завершена", "5 минут без речи"] in [call["argv"] for call in spook.notify.calls()]
