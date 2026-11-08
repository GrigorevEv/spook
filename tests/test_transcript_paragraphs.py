"""Сборка абзацев Транскрипта: склейка соседних Реплик, молчание, длина — через HTTP, файлы Заметок и поток событий.

Подменённый вход режет звук принудительно по `max_utterance_seconds`, поэтому Реплики идут вплотную;
молчание между ними создаётся отрезками, которые сценарный распознаватель считает тишиной.
"""

from __future__ import annotations

from conftest import NOTE_AFTER_START, note_text, speech


def cut_every(spook, seconds: float) -> None:
    """Укорачивает принудительный разрез, чтобы молчание между Репликами было короче 20 секунд."""
    spook.write_settings(max_utterance_seconds=seconds)


def test_adjacent_utterances_form_one_paragraph_with_one_time_at_start(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.", "Третья реплика.")
    spook.start_lecture()

    spook.audio.feed(speech(60))
    spook.wait_until(lambda: "Третья реплика." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Первая реплика. Вторая реплика. Третья реплика.\n"
    )


def test_page_updates_last_paragraph_in_place_instead_of_duplicating_it(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(speech(40))
        added = events.wait_for("paragraph", containing="Первая реплика.")
        updated = events.wait_for("paragraph-update", containing="Вторая реплика.")
    with spook.events() as events:
        events.next_event()  # состояние
        transcript = events.next_event()

    assert 'id="paragraph-1"' in added.data
    # Обновление адресовано тому же абзацу по id и заменяет его на странице целиком.
    assert 'id="paragraph-1"' in updated.data and 'hx-swap-oob="true"' in updated.data
    assert "<time>0:00:00</time> Первая реплика. Вторая реплика." in updated.data
    assert transcript.data.count("<p ") == 1 and "Первая реплика. Вторая реплика." in transcript.data


def test_silence_longer_than_10_seconds_starts_new_paragraph(spook):
    cut_every(spook, seconds=5)
    spook.recognizer.say("Слова.", "", "", "", "Ещё слова.")  # 15 секунд тишины между Репликами
    spook.start_lecture()

    spook.audio.feed(speech(25))
    spook.wait_until(lambda: "Ещё слова." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**0:00:00** Слова.\n"
        "\n**0:00:20** Ещё слова.\n"
    )


def test_silence_shorter_than_10_seconds_continues_paragraph(spook):
    cut_every(spook, seconds=5)
    spook.recognizer.say("Слова.", "", "Ещё слова.")  # 5 секунд тишины между Репликами
    spook.start_lecture()

    spook.audio.feed(speech(15))
    spook.wait_until(lambda: "Ещё слова." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + "\n**0:00:00** Слова. Ещё слова.\n"


def test_paragraph_longer_than_700_characters_closes_and_next_utterance_starts_new_one(spook):
    # Три Реплики по 250 знаков — 752 знака с пробелами: абзац перерос ~700 и закрылся.
    first, second, third, fourth = ("а" * 250, "б" * 250, "в" * 250, "Новый абзац.")
    spook.recognizer.say(first, second, third, fourth)
    spook.start_lecture()

    spook.audio.feed(speech(80))
    spook.wait_until(lambda: "Новый абзац." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        f"\n**0:00:00** {first} {second} {third}\n"
        "\n**0:01:00** Новый абзац.\n"
    )


def test_note_grows_only_by_appending_and_nothing_above_transcript_heading_changes(spook):
    spook.recognizer.say("Первая.", "Вторая.", "", "Третья.")
    spook.start_lecture()
    snapshots = [note_text(spook)]

    with spook.events() as events:
        for seconds, event, text in (
            (20, "paragraph", "Первая."),
            (20, "paragraph-update", "Вторая."),
            (40, "paragraph", "Третья."),
        ):
            spook.audio.feed(speech(seconds))
            events.wait_for(event, containing=text)
            snapshots.append(note_text(spook))

    for before, after in zip(snapshots, snapshots[1:]):
        # Уже записанное остаётся на месте байт в байт: меняется только конец файла.
        assert after.startswith(before.rstrip("\n")) and len(after) > len(before)
    assert all(snapshot.startswith(NOTE_AFTER_START) for snapshot in snapshots)
    assert snapshots[-1] == NOTE_AFTER_START + (
        "\n**0:00:00** Первая. Вторая.\n"
        "\n**0:01:00** Третья.\n"
    )
