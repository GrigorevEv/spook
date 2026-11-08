"""Файл настроек: умолчания из спецификации, частичный файл дополняется умолчаниями."""

from __future__ import annotations

from pathlib import Path

from spook.settings import load_settings


def test_settings_without_file_have_defaults_from_spec(tmp_path: Path):
    settings = load_settings(tmp_path / "нет такого файла.toml")

    assert settings.port == 8765
    assert settings.lecture.folder == Path.home() / "learn/wiki/lectures"
    assert settings.lecture.skill == "spook-lecture"
    assert settings.meeting.folder == Path.home() / "Documents/notes/работа"
    assert settings.meeting.skill == "spook-meeting"
    assert (settings.lecture.model, settings.lecture.effort) == ("opus", "high")
    assert settings.auto_stop_minutes == 5
    assert settings.terms_dictionary == Path.home() / "Documents/notes/работа/Словарь терминов.md"
    assert settings.recognition.max_utterance_seconds == 20
    # Детектор речи — итоги замера задачи 01 (docs/research/gigaam-benchmark.md).
    assert settings.recognition.speech_threshold == 0.5
    assert settings.recognition.min_silence_duration_ms == 500
    assert settings.recognition.min_speech_duration_ms == 250
    assert settings.recognition.speech_pad_ms == 100
    assert settings.recognition.forced_cut_min_pause_ms == 100
    assert (settings.sources.system, settings.sources.mic) == ("", "")
    assert settings.wiki == Path.home() / "learn/wiki"
    assert settings.programs.claude == str(Path.home() / ".local/bin/claude")
    assert settings.agent.timeout_minutes == 15
    assert settings.agent.wrapper == ""


def test_partial_settings_file_overrides_only_given_keys(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "port = 9000\n"
        "auto_stop_minutes = 7\n"
        "\n"
        "[lecture]\n"
        'folder = "~/lectures"\n'
        'effort = "medium"\n'
        "\n"
        "[programs]\n"
        'claude = "/opt/claude/bin/claude"\n',
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.port == 9000
    assert settings.auto_stop_minutes == 7
    assert settings.lecture.folder == Path.home() / "lectures"
    assert (settings.lecture.skill, settings.lecture.model, settings.lecture.effort) == ("spook-lecture", "opus", "medium")
    assert settings.meeting.folder == Path.home() / "Documents/notes/работа"
    assert settings.programs.claude == "/opt/claude/bin/claude"
    assert settings.programs.notify == "notify-send"


def test_agent_section_sets_timeout_and_wrapper_and_wiki_root_expands_home(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(
        'wiki = "~/vault/wiki"\n'
        "\n"
        "[agent]\n"
        "timeout_minutes = 20\n"
        'wrapper = "systemd-run --user --wait --pipe --collect --quiet --same-dir"\n'
        "\n"
        "[programs]\n"
        'claude = "~/bin/claude"\n',
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.wiki == Path.home() / "vault/wiki"
    assert settings.agent.timeout_minutes == 20
    assert settings.agent.wrapper == "systemd-run --user --wait --pipe --collect --quiet --same-dir"
    assert settings.programs.claude == str(Path.home() / "bin/claude")


def test_recognition_settings_default_to_model_chosen_by_benchmark(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    recognition = load_settings(tmp_path / "нет такого файла.toml").recognition

    assert recognition.model_dir == tmp_path / "data/spook/models/gigaam-v3-e2e-rnnt"
    assert recognition.model_revision == "322c3b29492673eb7d0b434bfa9dfb8653e34d02"
    assert recognition.quantization == "int8"
    assert (recognition.intra_op_threads, recognition.inter_op_threads) == (4, 1)


def test_recognition_settings_from_file(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text('[recognition]\nmodel_dir = "~/models/gigaam"\nintra_op_threads = 6\n', encoding="utf-8")

    recognition = load_settings(path).recognition

    assert recognition.model_dir == Path.home() / "models/gigaam"
    assert recognition.intra_op_threads == 6
    assert recognition.max_utterance_seconds == 20
