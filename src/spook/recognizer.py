"""Распознаватель спрятан за интерфейсом «отрезок звука → текст» (ADR-0002).

Настоящий распознаватель — GigaAM-v3 `e2e_rnnt` int8 на CPU через onnx-asr (выбор замера задачи 01,
`docs/research/gigaam-benchmark.md`). Модель скачивается один раз командой `spook prepare`
в каталог данных приложения и загружается в память при первом распознавании — то есть после
первого «Старт»; в простое служба модель не держит.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from typing import Any, Protocol

from spook.audio import SAMPLE_RATE, AudioSegment
from spook.settings import RecognitionSettings

log = logging.getLogger(__name__)

MODEL_NAME = "gigaam-v3-e2e-rnnt"  # имя модели в onnx-asr
MODEL_REPO = "istupakov/gigaam-v3-onnx"  # Hugging Face, MIT


class Recognizer(Protocol):
    def recognize(self, segment: AudioSegment) -> str:
        """Текст Реплики; пустая строка — речи в отрезке нет."""
        ...


class RecognitionUnavailable(Exception):
    """Распознавать нечем; текст — причина для пользователя."""


class MissingRecognizer:
    """Распознаватель, которому нечем распознавать."""

    def recognize(self, segment: AudioSegment) -> str:
        raise RecognitionUnavailable("Модель распознавания ещё не подключена")


def model_files(settings: RecognitionSettings) -> list[str]:
    """Файлы модели в каталоге `settings.model_dir`."""
    suffix = f".{settings.quantization}" if settings.quantization else ""
    return [
        "config.json",
        "v3_e2e_rnnt_vocab.txt",
        *(f"v3_e2e_rnnt_{part}{suffix}.onnx" for part in ("encoder", "decoder", "joint")),
    ]


def model_is_present(settings: RecognitionSettings) -> bool:
    return all((settings.model_dir / name).is_file() for name in model_files(settings))


def prepare_model(settings: RecognitionSettings) -> bool:
    """Скачивает модель в `settings.model_dir`, если её там нет; True — скачана сейчас.

    Скачивание идёт во временный соседний каталог, и файлы переносятся на место только после
    того, как скачаны все: оборванная загрузка не выдаёт себя за готовую модель. При неудаче —
    `RecognitionUnavailable` с причиной.
    """
    if model_is_present(settings):
        return False
    from huggingface_hub import snapshot_download

    target = settings.model_dir
    partial = target.with_name(target.name + ".partial")
    try:
        snapshot_download(
            MODEL_REPO,
            revision=settings.model_revision,
            local_dir=partial,
            allow_patterns=model_files(settings),
        )
        target.mkdir(parents=True, exist_ok=True)
        for name in model_files(settings):
            (partial / name).replace(target / name)
    except Exception as error:
        raise RecognitionUnavailable(f"Модель распознавания не скачана в {target}: {error}") from error
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return True


class GigaamRecognizer:
    """GigaAM-v3 `e2e_rnnt` через onnx-asr; модель загружается при первом `recognize`.

    Очередь распознавания одна, но загрузка всё равно под замком: распознаватель не знает,
    из скольких потоков его зовут.
    """

    def __init__(self, settings: RecognitionSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._model: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def recognize(self, segment: AudioSegment) -> str:
        if len(segment.samples) == 0:
            return ""
        model = self._loaded_model()
        return model.recognize(segment.samples, sample_rate=SAMPLE_RATE)

    def _loaded_model(self) -> Any:
        with self._lock:
            if self._model is None:
                self._model = self._load()
            return self._model

    def _load(self) -> Any:
        settings = self._settings
        if not model_is_present(settings):
            raise RecognitionUnavailable(
                f"Модель распознавания не скачана в {settings.model_dir}: выполните `spook prepare`"
            )
        import onnx_asr
        import onnxruntime

        log.info("Модель распознавания загружается: %s", settings.model_dir)
        began = time.monotonic()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = settings.intra_op_threads
        options.inter_op_num_threads = settings.inter_op_threads
        model = onnx_asr.load_model(
            MODEL_NAME,
            settings.model_dir,
            quantization=settings.quantization or None,
            sess_options=options,
        )
        log.info("Модель распознавания загружена за %.1f с", time.monotonic() - began)
        return model
