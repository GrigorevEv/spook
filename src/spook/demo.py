"""Показ «на подменах»: настоящая служба, но вход звука — образцы, распознаватель — сценарный.

`spook demo` поднимает тот же сервер, что `spook serve`, с подменами из `fakes.py`: пока идёт
Сессия, в подменённый вход в реальном времени подаётся «речь» из образца, а Реплики получают
заготовленный текст по кругу — текст в браузере не кончается. Вместо Claude Code — заглушка,
которая через несколько секунд дописывает Выжимку; уведомлений нет. Заметки и журналы пишутся
во временную папку, Хранилища пользователя не трогаются. Звук на диск не пишется (ADR-0001).
"""

from __future__ import annotations

import itertools
import logging
import signal
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

from spook.audio import SAMPLE_RATE, AudioSegment, Source
from spook.clock import SystemClock
from spook.fakes import FakeAudioInput, LoudnessSpeechDetector
from spook.serve import HOST, make_server
from spook.settings import KindSettings, ProgramSettings, RecognitionSettings, Settings
from spook.web import create_app

SCRIPT = (
    "Сегодня поговорим о том, как Spook слушает звук на компьютере и превращает его в текст.",
    "Сессия начинается по кнопке «Старт» и бывает двух видов: Встреча или Лекция.",
    "В Лекции захватывается только Системный звук, в Встрече — ещё и Микрофон.",
    "Речь режется по паузам на Реплики, каждая Реплика распознаётся целиком.",
    "Звук живёт только в памяти: ни файлов, ни временных файлов на диске.",
    "Соседние Реплики складываются в абзацы с временем от начала Сессии.",
    "Заметка появляется в Хранилище при «Старт» и дописывается по ходу Сессии.",
    "Сессия живёт в службе, а не во вкладке: вкладку можно закрыть и открыть заново.",
    "После «Стоп» очередь дорабатывается, а Агент пишет Выжимку наверху Заметки.",
    "В облако уходит только текст Транскрипта — и только Агенту.",
)

UTTERANCE_SECONDS = 3.0  # Реплики показа короткие, чтобы текст появлялся каждые несколько секунд
CHUNK_SECONDS = 0.5
AGENT_SECONDS = 3.0  # заглушка Агента «работает» столько, чтобы состояние успело показаться

AGENT_STUB = f'''\
"""Заглушка Claude Code для показа: выждать и дописать Выжимку над «## Транскрипт»."""
import sys
import time
from pathlib import Path

time.sleep({AGENT_SECONDS})
prompt = sys.argv[sys.argv.index("-p") + 1]
note = Path(prompt.split(" ", 1)[1])
summary = "## О чём\\n\\nПоказ на подменах: здесь Выжимку вместо Агента пишет заглушка.\\n\\n"
note.write_text(note.read_text(encoding="utf-8").replace("## Транскрипт", summary + "## Транскрипт", 1), encoding="utf-8")
print("Заглушка Агента показа: Выжимка написана.")
'''


class CyclingRecognizer:
    """Сценарный распознаватель показа: заготовленные Реплики по кругу."""

    def __init__(self, *texts: str) -> None:
        self._texts = itertools.cycle(texts)
        self._lock = threading.Lock()

    def recognize(self, segment: AudioSegment) -> str:
        with self._lock:
            return next(self._texts)


def demo_programs(folder: Path) -> ProgramSettings:
    """Внешние программы показа: вместо Claude Code — заглушка в `folder`, вместо уведомлений — `true`."""
    script = folder / "claude-stub.py"
    script.write_text(AGENT_STUB, encoding="utf-8")
    launcher = folder / "claude"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    return ProgramSettings(claude=str(launcher), notify="true")


def demo_settings(folder: Path, port: int) -> Settings:
    """Настройки показа: всё, что пишется на диск, — внутри `folder`; настоящие программы не вызываются."""
    return Settings(
        port=port,
        terms_dictionary=folder / "Словарь терминов.md",
        meeting=KindSettings(folder / "работа", "spook-meeting"),
        lecture=KindSettings(folder / "lectures", "spook-lecture"),
        recognition=RecognitionSettings(max_utterance_seconds=UTTERANCE_SECONDS),
        programs=demo_programs(folder),
    )


def speech_sample(seconds: float) -> np.ndarray:
    """Образец «речи» для подменённого входа: шум заданной длины."""
    count = int(seconds * SAMPLE_RATE)
    return np.random.default_rng(count).uniform(-0.3, 0.3, count).astype(np.float32)


def feed_in_real_time(audio: FakeAudioInput, stop: threading.Event) -> None:
    """Подаёт образец порциями в реальном времени в оба Источника; без идущего захвата Источника
    (нет Сессии, Лекция для Микрофона) звук пропадает, как в настоящей системе."""
    chunk = speech_sample(CHUNK_SECONDS)
    while not stop.wait(CHUNK_SECONDS):
        for source in Source:
            audio.feed(chunk, source)


def _exit_quietly(signum: int, frame: object) -> None:
    raise SystemExit(0)


def run_demo(port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # После мягкой остановки uvicorn заново возбуждает пойманный сигнал; штатные обработчики убили бы
    # процесс (SIGTERM) или вывалили бы KeyboardInterrupt (Ctrl+C) до уборки временной папки.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _exit_quietly)
        signal.signal(signal.SIGINT, _exit_quietly)
    with tempfile.TemporaryDirectory(prefix="spook-demo-") as folder:
        settings = demo_settings(Path(folder), port)
        audio = FakeAudioInput()
        app = create_app(
            load_settings=lambda: settings,
            audio_input=audio,
            recognizer=CyclingRecognizer(*SCRIPT),
            clock=SystemClock(),
            state_dir=Path(folder) / "state",
            speech_detector=LoudnessSpeechDetector,
        )
        stop = threading.Event()
        feeder = threading.Thread(target=feed_in_real_time, args=(audio, stop), name="spook-demo-audio", daemon=True)
        feeder.start()
        print(f"Показ на подменах: http://{HOST}:{port}/ — Заметки во временной папке {folder}", flush=True)
        try:
            make_server(app, port=port).run()
        finally:
            stop.set()
            feeder.join(timeout=CHUNK_SECONDS * 2)
