"""Команда подготовки `spook prepare`: скачивает модели распознавания и детектора речи (Silero) один раз;
её вызывает установка. Каталог детектора — `$XDG_DATA_HOME/spook/models/silero-vad`.

Быстрые тесты идут без сети: `HF_HUB_OFFLINE=1` запрещает скачивание, так что попытка
скачать там, где модель уже есть, провалила бы тест. Настоящее скачивание — вне автотестов.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MODEL_FILES = (
    "config.json",
    "v3_e2e_rnnt_vocab.txt",
    "v3_e2e_rnnt_encoder.int8.onnx",
    "v3_e2e_rnnt_decoder.int8.onnx",
    "v3_e2e_rnnt_joint.int8.onnx",
)


def prepare(tmp_path: Path, model_dir: Path) -> subprocess.CompletedProcess[str]:
    settings = tmp_path / "settings.toml"
    settings.write_text(f'[recognition]\nmodel_dir = "{model_dir}"\n', encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "spook.cli", "prepare", "--settings", str(settings)],
        env={
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "HF_HOME": str(tmp_path / "hf"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def put_recognition_model(model_dir: Path) -> None:
    model_dir.mkdir(parents=True)
    for name in MODEL_FILES:
        (model_dir / name).write_text(f"готовый {name}", encoding="utf-8")


def put_speech_detector_model(tmp_path: Path) -> Path:
    vad_dir = tmp_path / "data" / "spook" / "models" / "silero-vad"
    vad_dir.mkdir(parents=True)
    (vad_dir / "silero_vad.onnx").write_text("готовый silero", encoding="utf-8")
    return vad_dir


def test_prepare_does_nothing_when_both_models_are_already_present(tmp_path):
    model_dir = tmp_path / "data" / "models" / "gigaam-v3-e2e-rnnt"
    put_recognition_model(model_dir)
    vad_dir = put_speech_detector_model(tmp_path)

    result = prepare(tmp_path, model_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("уже есть") == 2, result.stdout
    assert sorted(path.name for path in model_dir.iterdir()) == sorted(MODEL_FILES)
    assert all((model_dir / name).read_text(encoding="utf-8") == f"готовый {name}" for name in MODEL_FILES)
    assert [path.name for path in vad_dir.iterdir()] == ["silero_vad.onnx"]
    assert (vad_dir / "silero_vad.onnx").read_text(encoding="utf-8") == "готовый silero"


def test_prepare_without_network_explains_missing_speech_detector_and_leaves_no_partial_model(tmp_path):
    model_dir = tmp_path / "data" / "models" / "gigaam-v3-e2e-rnnt"
    put_recognition_model(model_dir)
    vad_models = tmp_path / "data" / "spook" / "models"

    result = prepare(tmp_path, model_dir)

    assert result.returncode != 0
    assert "Модель детектора речи не скачана" in result.stderr
    assert not vad_models.exists() or list(vad_models.iterdir()) == []


def test_prepare_without_network_explains_failure_and_leaves_no_partial_model(tmp_path):
    models = tmp_path / "data" / "models"

    result = prepare(tmp_path, models / "gigaam-v3-e2e-rnnt")

    assert result.returncode != 0
    assert "Модель распознавания не скачана" in result.stderr
    assert not models.exists() or list(models.iterdir()) == []
