"""Проверка, что sherpa-onnx с архивом punct-giga-am-v3 даёт тот же текст, что onnx-asr (один образец).

Запуск:
    uv run --no-project --with sherpa-onnx --with numpy python sherpa_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench import SherpaEngine  # noqa: E402
from benchlib import SAMPLES_DIR, load_json, read_wav, wer  # noqa: E402

if __name__ == "__main__":
    manifest = load_json(SAMPLES_DIR / "samples.json")
    t0 = time.perf_counter()
    engine = SherpaEngine(4, "int8")
    print(f"загрузка {time.perf_counter() - t0:.2f} с")
    for name in ("utt_05s.wav", "utt_10s.wav", "synth_numbers.wav"):
        x = read_wav(SAMPLES_DIR / name)
        t0 = time.perf_counter()
        text = engine.recognize(x)
        print(f"{name}: {time.perf_counter() - t0:.2f} с, WER {wer(manifest[name]['text'], text):.3f}\n   {text}")
