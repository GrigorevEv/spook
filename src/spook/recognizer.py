from __future__ import annotations

import logging
import shutil
import threading
import time
from typing import Any, Protocol

from spook.audio import SAMPLE_RATE, AudioSegment
from spook.settings import RecognitionSettings

log = logging.getLogger(__name__)

MODEL_NAME = "gigaam-v3-e2e-rnnt"
MODEL_REPO = "istupakov/gigaam-v3-onnx"


class Recognizer(Protocol):
    def check_available(self) -> None:
        ...

    def recognize(self, segment: AudioSegment) -> str:
        ...


class RecognitionUnavailable(Exception):
    pass


class MissingRecognizer:
    def check_available(self) -> None:
        raise RecognitionUnavailable("Модель распознавания ещё не подключена")

    def recognize(self, segment: AudioSegment) -> str:
        raise RecognitionUnavailable("Модель распознавания ещё не подключена")


def model_files(settings: RecognitionSettings) -> list[str]:
    suffix = f".{settings.quantization}" if settings.quantization else ""
    return [
        "config.json",
        "v3_e2e_rnnt_vocab.txt",
        *(f"v3_e2e_rnnt_{part}{suffix}.onnx" for part in ("encoder", "decoder", "joint")),
    ]


def model_is_present(settings: RecognitionSettings) -> bool:
    return all((settings.model_dir / name).is_file() for name in model_files(settings))


def prepare_model(settings: RecognitionSettings) -> bool:
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
    def __init__(self, settings: RecognitionSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._model: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def check_available(self) -> None:
        if not model_is_present(self._settings):
            raise RecognitionUnavailable(
                f"Модель распознавания не скачана в {self._settings.model_dir}: выполните `spook prepare`"
            )

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
        self.check_available()
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
