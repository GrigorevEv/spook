from __future__ import annotations

import re

from test_prepare import MODEL_FILES, prepare_offline, put_recognition_model, put_speech_detector_model


def failure_line(prefix: str, stderr: str) -> str:
    match = re.search(rf"^{re.escape(prefix)}(?P<reason>.+)$", stderr, re.MULTILINE)
    assert match is not None, stderr
    return match["reason"].strip()


def test_prepare_names_folder_of_each_model_that_is_already_present(tmp_path):
    model_dir = tmp_path / "data" / "models" / "gigaam-v3-e2e-rnnt"
    put_recognition_model(model_dir)
    vad_dir = put_speech_detector_model(tmp_path)

    result = prepare_offline(tmp_path, model_dir)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert f"Модель распознавания уже есть: {model_dir}" in lines, result.stdout
    assert f"Модель детектора речи уже есть: {vad_dir}" in lines, result.stdout
    assert all((model_dir / name).read_text(encoding="utf-8") == f"готовый {name}" for name in MODEL_FILES)
    assert (vad_dir / "silero_vad.onnx").read_text(encoding="utf-8") == "готовый silero"


def test_prepare_without_network_names_folder_and_reason_when_recognition_model_is_missing(tmp_path):
    models = tmp_path / "data" / "models"
    model_dir = models / "gigaam-v3-e2e-rnnt"

    result = prepare_offline(tmp_path, model_dir)

    assert result.returncode != 0
    assert failure_line(f"Модель распознавания не скачана в {model_dir}: ", result.stderr)
    assert not models.exists() or list(models.iterdir()) == []


def test_prepare_without_network_names_folder_and_reason_when_speech_detector_model_is_missing(tmp_path):
    model_dir = tmp_path / "data" / "models" / "gigaam-v3-e2e-rnnt"
    put_recognition_model(model_dir)
    vad_models = tmp_path / "data" / "spook" / "models"

    result = prepare_offline(tmp_path, model_dir)

    assert result.returncode != 0
    assert failure_line(f"Модель детектора речи не скачана в {vad_models / 'silero-vad'}: ", result.stderr)
    assert not vad_models.exists() or list(vad_models.iterdir()) == []
