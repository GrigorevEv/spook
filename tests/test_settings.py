from __future__ import annotations

from pathlib import Path

import pytest

from spook.settings import load_settings


def test_settings_without_file_have_defaults_from_spec(tmp_path: Path):
    settings = load_settings(tmp_path / "нет такого файла.toml")

    assert settings.port == 8765
    assert settings.wisp == Path.home() / "code/personal/wisp"
    assert settings.meeting.skill == "spook-meeting"
    assert (settings.meeting.model, settings.meeting.effort) == ("gpt-6.2-astra", "high")
    assert settings.auto_stop_minutes == 5
    assert settings.terms_dictionary == Path.home() / "Documents/notes/работа/Словарь терминов.md"
    assert settings.recognition.max_utterance_seconds == 20
    assert settings.recognition.speech_threshold == 0.5
    assert settings.recognition.min_silence_duration_ms == 500
    assert settings.recognition.min_speech_duration_ms == 250
    assert settings.recognition.speech_pad_ms == 100
    assert settings.recognition.forced_cut_min_pause_ms == 100
    assert (settings.sources.system, settings.sources.mic) == ("", "")
    assert settings.programs.codex == str(Path.home() / ".local/bin/codex")
    assert settings.agent.timeout_minutes == 15
    assert settings.agent.wrapper == ""
    assert settings.agent.skills_dir == Path.home() / ".agents/skills"


def test_partial_settings_file_overrides_only_given_keys(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(
        'wisp = "~/новый Wisp"\n'
        "port = 9000\n"
        "auto_stop_minutes = 7\n"
        "\n"
        "[meeting]\n"
        'folder = "~/meetings"\n'
        'effort = "medium"\n'
        "\n"
        "[programs]\n"
        'codex = "/opt/codex/bin/codex"\n',
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.port == 9000
    assert settings.auto_stop_minutes == 7
    assert settings.wisp == Path.home() / "новый Wisp"
    assert settings.kind_folders()[0][1] == settings.wisp / "html"
    assert (settings.meeting.skill, settings.meeting.model, settings.meeting.effort) == ("spook-meeting", "gpt-6.2-astra", "medium")
    assert settings.programs.codex == "/opt/codex/bin/codex"
    assert settings.programs.notify == "notify-send"


def test_agent_section_sets_timeout_and_wrapper_and_wisp_root_expands_home(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(
        'wisp = "~/vault/wisp"\n'
        "\n"
        "[agent]\n"
        "timeout_minutes = 20\n"
        'wrapper = "systemd-run --user --wait --pipe --collect --quiet --same-dir"\n'
        "\n"
        "[programs]\n"
        'codex = "~/bin/codex"\n',
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.wisp == Path.home() / "vault/wisp"
    assert settings.agent.timeout_minutes == 20
    assert settings.agent.wrapper == "systemd-run --user --wait --pipe --collect --quiet --same-dir"
    assert settings.programs.codex == str(Path.home() / "bin/codex")


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


@pytest.mark.parametrize("previous_model", ["sonnet", "opus[1m]", "claude-haiku-4-5"])
def test_old_claude_settings_select_codex_default_and_preserve_user_file(tmp_path, previous_model):
    path = tmp_path / "settings.toml"
    text = (
        f'[meeting]\nmodel = "{previous_model}"\neffort = "medium"\n'
        '\n[programs]\nclaude = "/tmp/old-claude"\n'
    )
    path.write_text(text, encoding="utf-8")

    settings = load_settings(path)

    assert (settings.meeting.model, settings.meeting.effort) == ("gpt-6.2-astra", "high")
    assert settings.programs.codex == str(Path.home() / ".local/bin/codex")
    assert path.read_text(encoding="utf-8") == text
