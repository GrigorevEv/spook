"""Общие помощники замера: каталоги, нагрузка машины, wav, WER.

Скрипты задачи 01 одноразовые, но воспроизводимые. Данные (модели, звук) лежат
вне репозитория, в BENCH_DIR (по умолчанию ~/.local/share/spook/bench).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

BENCH_DIR = Path(os.environ.get("SPOOK_BENCH_DIR", "~/.local/share/spook/bench")).expanduser()
SAMPLES_DIR = BENCH_DIR / "samples"
MODELS_DIR = BENCH_DIR / "models"
RESULTS_DIR = BENCH_DIR / "results"
SAMPLE_RATE = 16_000


def loadavg() -> tuple[float, float, float]:
    with open("/proc/loadavg") as f:
        a, b, c = f.read().split()[:3]
    return float(a), float(b), float(c)


def wait_for_quiet(limit: float = 6.0, attempts: int = 10, pause_s: float = 60.0) -> str:
    """Ждёт, пока минутная нагрузка не упадёт ниже limit (до attempts раз по pause_s).

    Возвращает строку /proc/loadavg (три числа) на момент начала замера.
    """
    for i in range(attempts):
        one, five, fifteen = loadavg()
        if one <= limit:
            return f"{one:.2f} {five:.2f} {fifteen:.2f}"
        print(f"[нагрузка {one:.2f} > {limit}] жду {pause_s:.0f} с ({i + 1}/{attempts})", file=sys.stderr, flush=True)
        time.sleep(pause_s)
    one, five, fifteen = loadavg()
    return f"{one:.2f} {five:.2f} {fifteen:.2f} (не дождался тишины)"


def rss_mb() -> float:
    """Текущий RSS процесса в МБ (из /proc/self/statm)."""
    with open("/proc/self/statm") as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024


def peak_rss_mb() -> float:
    """Пиковый RSS процесса в МБ (VmHWM из /proc/self/status)."""
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return float("nan")


def read_wav(path: Path | str) -> np.ndarray:
    """Читает 16 кГц моно PCM16 wav в float32 [-1, 1]."""
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE, (path, w.getframerate())
        assert w.getnchannels() == 1, (path, w.getnchannels())
        assert w.getsampwidth() == 2, (path, w.getsampwidth())
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path: Path | str, samples: np.ndarray) -> None:
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def duration_s(samples: np.ndarray) -> float:
    return len(samples) / SAMPLE_RATE


_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def normalize_words(text: str) -> list[str]:
    """Слова в нижнем регистре без пунктуации, ё → е (для WER)."""
    return [w.lower().replace("ё", "е") for w in _WORD_RE.findall(text)]


def wer(reference: str, hypothesis: str) -> float:
    """Доля ошибок в словах (Левенштейн по словам) относительно эталона."""
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return float("nan")
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def save_json(path: Path | str, data: object) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"записано: {path}", file=sys.stderr)


def load_json(path: Path | str) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
