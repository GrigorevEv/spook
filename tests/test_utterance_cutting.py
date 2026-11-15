from __future__ import annotations

import numpy as np
import pytest

from conftest import NOTE_AFTER_START, SAMPLE_RATE, note_text, speech


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def heard_starts_and_durations(spook) -> list[tuple[float, float]]:
    return [(round(item.start, 3), round(item.duration, 3)) for item in spook.recognizer.heard]


def test_phrase_pause_phrase_gives_two_utterances_closed_by_pause_without_stop(spook):
    spook.recognizer.say("Первая фраза.", "Вторая фраза.")
    spook.start_lecture()

    spook.audio.feed(np.concatenate([silence(1), speech(3), silence(1.5), speech(4), silence(1)]))
    spook.wait_until(lambda: "Вторая фраза." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(0.9, 3.2), (5.4, 4.2)]
    assert note_text(spook) == NOTE_AFTER_START + "\n**0:00:00** Первая фраза. Вторая фраза.\n"


def test_silence_gives_no_utterances_and_session_without_utterances_leaves_no_note(spook):
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(silence(30))
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert heard_starts_and_durations(spook) == []
    assert not (spook.lectures_dir / "2026-11-06 14-02 Лекция.md").exists()


def test_pause_inside_phrase_shorter_than_closing_pause_does_not_cut_it(spook):
    spook.recognizer.say("Фраза с заминкой.")
    spook.start_lecture()

    spook.audio.feed(np.concatenate([speech(3), silence(0.5), speech(3), silence(1)]))
    spook.wait_until(lambda: "Фраза с заминкой." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(0.0, 6.6)]


def test_speech_without_closing_pause_longer_than_20_seconds_is_cut_at_last_short_pause(spook):
    spook.recognizer.say("До заминки.", "После заминки.")
    spook.start_lecture()

    spook.audio.feed(np.concatenate([speech(15), silence(0.3), speech(10)]))
    spook.wait_until(lambda: "До заминки." in note_text(spook))
    spook.stop()
    spook.wait_until(lambda: "После заминки." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(0.0, 15.1), (15.2, 10.1)]


def test_speech_without_any_pause_is_cut_hard_at_20_seconds(spook):
    spook.recognizer.say("Первые двадцать секунд.", "Вторые двадцать секунд.", "Хвост.")
    spook.start_lecture()

    spook.audio.feed(speech(45))
    spook.wait_until(lambda: "Вторые двадцать секунд." in note_text(spook))
    spook.stop()
    spook.wait_until(lambda: "Хвост." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(0.0, 20.0), (20.0, 20.0), (40.0, 5.0)]


def test_stop_closes_current_utterance_during_pause_and_sends_it_to_recognition(spook):
    spook.recognizer.say("Последняя фраза.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(np.concatenate([speech(3), silence(0.4)]))
        spook.stop()
        events.wait_for("state", containing="нет Сессии")

    assert heard_starts_and_durations(spook) == [(0.0, 3.1)]
    assert "**0:00:00** Последняя фраза." in note_text(spook)


def test_pause_closes_current_utterance_during_pause_and_sends_it_to_recognition(spook):
    spook.recognizer.say("Фраза перед Паузой.")
    spook.start_lecture()

    with spook.events() as events:
        spook.audio.feed(np.concatenate([speech(2), silence(0.2)]))
        spook.pause()
        events.wait_for("paragraph", containing="Фраза перед Паузой.")

    assert heard_starts_and_durations(spook) == [(0.0, 2.1)]


def test_click_shorter_than_250_ms_is_not_an_utterance(spook):
    spook.recognizer.say("Настоящая фраза.")
    spook.start_lecture()

    spook.audio.feed(np.concatenate([silence(1), speech(0.2), silence(1), speech(2), silence(1)]))
    spook.wait_until(lambda: "Настоящая фраза." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(2.1, 2.2)]


def test_detector_parameters_from_settings_apply_from_next_session(spook):
    spook.write_settings(recognition={"min_silence_duration_ms": 200, "speech_pad_ms": 50})
    spook.recognizer.say("Половина.", "Другая половина.")
    spook.start_lecture()

    spook.audio.feed(np.concatenate([speech(3), silence(0.5), speech(3), silence(1)]))
    spook.wait_until(lambda: "Другая половина." in note_text(spook))

    assert heard_starts_and_durations(spook) == [(0.0, 3.05), (3.45, 3.1)]
