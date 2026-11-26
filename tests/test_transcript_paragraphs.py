from __future__ import annotations

from conftest import NO_SPEECH, NOTE_AFTER_START, note_text, speech


def cut_every(spook, seconds: float) -> None:
    spook.write_settings(max_utterance_seconds=seconds)


def test_adjacent_utterances_form_one_paragraph_with_one_time_at_start(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.", "Третья реплика.")
    spook.start_meeting()

    spook.feed_system(speech(60))
    spook.wait_until(lambda: "Третья реплика." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Первая реплика. Вторая реплика. Третья реплика.\n"
    )


def test_page_updates_last_paragraph_in_place_instead_of_duplicating_it(spook):
    spook.recognizer.say("Первая реплика.", "Вторая реплика.")
    spook.start_meeting()

    with spook.events() as events:
        spook.feed_system(speech(40))
        added = events.wait_for("paragraph", containing="Первая реплика.")
        updated = events.wait_for("paragraph-update", containing="Вторая реплика.")
    with spook.events() as events:
        events.next_event()
        transcript = events.next_event()

    assert 'id="paragraph-1"' in added.data
    assert 'id="paragraph-1"' in updated.data and 'hx-swap-oob="true"' in updated.data
    assert '<time>14:02</time> <b class="speaker system">Собеседники</b> Первая реплика. Вторая реплика.' in updated.data
    assert transcript.data.count("<p ") == 1 and "Первая реплика. Вторая реплика." in transcript.data


def test_silence_longer_than_10_seconds_starts_new_paragraph(spook):
    cut_every(spook, seconds=5)
    spook.recognizer.say("Слова.", NO_SPEECH, NO_SPEECH, NO_SPEECH, "Ещё слова.")
    spook.start_meeting()

    spook.feed_system(speech(25))
    spook.wait_until(lambda: "Ещё слова." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Слова.\n"
        "\n**14:02 Собеседники:** Ещё слова.\n"
    )


def test_silence_shorter_than_10_seconds_continues_paragraph(spook):
    cut_every(spook, seconds=5)
    spook.recognizer.say("Слова.", NO_SPEECH, "Ещё слова.")
    spook.start_meeting()

    spook.feed_system(speech(15))
    spook.wait_until(lambda: "Ещё слова." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + "\n**14:02 Собеседники:** Слова. Ещё слова.\n"


def test_paragraph_longer_than_700_characters_closes_and_next_utterance_starts_new_one(spook):
    first, second, third, fourth = ("а" * 250, "б" * 250, "в" * 250, "Новый абзац.")
    spook.recognizer.say(first, second, third, fourth)
    spook.start_meeting()

    spook.feed_system(speech(80))
    spook.wait_until(lambda: "Новый абзац." in note_text(spook))

    assert note_text(spook) == NOTE_AFTER_START + (
        f"\n**14:02 Собеседники:** {first} {second} {third}\n"
        "\n**14:03 Собеседники:** Новый абзац.\n"
    )


def test_note_grows_only_by_appending_and_nothing_above_transcript_heading_changes(spook):
    spook.recognizer.say("Первая.", "Вторая.", NO_SPEECH, "Третья.")
    spook.start_meeting()
    snapshots = [note_text(spook)]

    with spook.events() as events:
        for seconds, event, text in (
            (20, "paragraph", "Первая."),
            (20, "paragraph-update", "Вторая."),
            (40, "paragraph", "Третья."),
        ):
            spook.feed_system(speech(seconds))
            events.wait_for(event, containing=text)
            snapshots.append(note_text(spook))

    for before, after in zip(snapshots, snapshots[1:]):
        assert after.startswith(before.rstrip("\n")) and len(after) > len(before)
    assert all(snapshot.startswith(NOTE_AFTER_START) for snapshot in snapshots)
    assert snapshots[-1] == NOTE_AFTER_START + (
        "\n**14:02 Собеседники:** Первая. Вторая.\n"
        "\n**14:03 Собеседники:** Третья.\n"
    )
