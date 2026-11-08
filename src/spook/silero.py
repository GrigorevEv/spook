"""Настоящий детектор речи — Silero VAD через onnx-asr (выбор задачи 01, `docs/research/gigaam-benchmark.md`).

Модель маленькая (около 2 МБ): onnx-asr скачивает её сам при первой загрузке в каталог данных
пользователя (`~/.local/share/spook/models/silero-vad`) и дальше берёт оттуда. Загружается при первом
«Старт»: в простое служба только ждёт. Одна модель на все потоки звука, состояние сети — у каждого своё.
Кадр — 512 отсчётов (32 мс) при 16 кГц; спереди к нему, как в onnx-asr, приложены 64 отсчёта предыдущего.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import numpy as np

from spook.audio import SAMPLE_RATE
from spook.settings import data_dir

FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64
MODEL_FILE = "silero_vad.onnx"


def default_model_dir() -> Path:
    return data_dir() / "models" / "silero-vad"


class SileroSpeechDetector:
    """Детектор речи одного потока звука на общей модели Silero."""

    frame_samples = FRAME_SAMPLES

    def __init__(self, model: Any) -> None:
        self._model = model
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def speech_probability(self, frame: np.ndarray) -> float:
        frame = np.asarray(frame, dtype=np.float32)
        window = np.concatenate([self._context, frame])[None, :]
        output, self._state = self._model.run(
            ["output", "stateN"],
            {"input": window, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)},
        )
        self._context = frame[-CONTEXT_SAMPLES:]
        return float(output[0, 0])


class SileroSpeechDetection:
    """Создаёт детекторы Silero; модель загружается (и при нужде скачивается) при первом детекторе."""

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir or default_model_dir()
        self._model: Any = None
        self._lock = threading.Lock()

    def __call__(self) -> SileroSpeechDetector:
        with self._lock:
            if self._model is None:
                self._model = load_model(self._model_dir)
            return SileroSpeechDetector(self._model)


def model_is_present(model_dir: Path | None = None) -> bool:
    return ((model_dir or default_model_dir()) / MODEL_FILE).is_file()


def prepare_model(model_dir: Path | None = None) -> bool:
    """Скачивает модель Silero в `model_dir`, если её там нет; True — скачана сейчас. Её вызывает
    `spook prepare`, чтобы «Старт» не зависел от сети. Скачивание идёт во временный соседний каталог,
    на место переносится готовый файл. При неудаче — `RecognitionUnavailable` с причиной."""
    from spook.recognizer import RecognitionUnavailable

    target = model_dir or default_model_dir()
    if model_is_present(target):
        return False
    partial = target.with_name(target.name + ".partial")
    try:
        import onnx_asr

        onnx_asr.load_vad("silero", partial)
        target.mkdir(parents=True, exist_ok=True)
        (partial / MODEL_FILE).replace(target / MODEL_FILE)
    except Exception as error:
        raise RecognitionUnavailable(f"Модель детектора речи не скачана в {target}: {error}") from error
    finally:
        shutil.rmtree(partial, ignore_errors=True)
        for empty in (target, target.parent):  # не оставлять пустых каталогов после неудачи
            if empty.is_dir() and not any(empty.iterdir()):
                empty.rmdir()
    return True


def load_model(model_dir: Path) -> Any:
    """Сессия onnxruntime с моделью Silero; модели нет — onnx-asr скачивает её в `model_dir`."""
    import onnxruntime

    if not (model_dir / MODEL_FILE).is_file():
        import onnx_asr

        onnx_asr.load_vad("silero", model_dir)
    options = onnxruntime.SessionOptions()
    # Модель крошечная и считается по кадру: лишние потоки только мешали бы распознавателю.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(
        str(model_dir / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"]
    )
