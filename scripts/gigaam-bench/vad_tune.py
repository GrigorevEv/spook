"""Подбор параметров детектора речи (Silero VAD через onnx-asr) и проверка качества на окнах.

Что делается:
1. Порог паузы: на образцах «фраза — пауза — фраза» (паузы 300…1500 мс) считаем, сколько Реплик даёт
   детектор при разных min_silence_duration_ms; ищем наименьший порог, при котором фразы не рвутся внутри.
2. Принудительный разрез: непрерывная речь 48 с без пауз → max_speech_duration_s=20; смотрим длины
   отрезков и качество (WER) склеенного текста против эталона.
3. Качество на окнах: Реплика 20 с целиком против нарезки детектором на окна ≤ 5 и ≤ 10 с и против
   слепой нарезки на 2,5 / 5 / 10 с (WER против эталонного текста).
4. Пунктуация и числа: тексты образцов с числительными (RuLS) и синтеза Piper; считаем цифры и знаки.
5. Скорость детектора на 300 с звука.

Запуск:
    uv run --no-project --with "onnx-asr[cpu,hub]" --with numpy python vad_tune.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from benchlib import MODELS_DIR, RESULTS_DIR, SAMPLE_RATE, SAMPLES_DIR, duration_s, load_json, read_wav, save_json, wait_for_quiet, wer  # noqa: E402

ONNX_ASR_DIR = MODELS_DIR / "onnx-asr" / "gigaam-v3-e2e-rnnt"
VAD_DIR = MODELS_DIR / "onnx-asr" / "silero-vad"
THREADS = 4


def segments(vad, x: np.ndarray, **opts) -> list[tuple[float, float]]:
    """Границы речи (с) по детектору без распознавания."""
    it = vad.segment_batch(x[None, :], np.array([len(x)], dtype=np.int64), SAMPLE_RATE, **opts)
    return [(s / SAMPLE_RATE, e / SAMPLE_RATE) for s, e in next(it)]


def fixed_windows(x: np.ndarray, win_s: float) -> list[np.ndarray]:
    n = int(win_s * SAMPLE_RATE)
    return [x[i : i + n] for i in range(0, len(x), n) if len(x[i : i + n]) > SAMPLE_RATE // 2]


def main() -> None:
    import onnx_asr
    import onnxruntime as rt

    so = rt.SessionOptions()
    so.intra_op_num_threads = THREADS
    so.inter_op_num_threads = 1
    model = onnx_asr.load_model("gigaam-v3-e2e-rnnt", ONNX_ASR_DIR, quantization="int8", sess_options=so)
    vad = onnx_asr.load_vad("silero", VAD_DIR, sess_options=so)
    manifest = load_json(SAMPLES_DIR / "samples.json")
    report: dict = {"threads": THREADS, "loadavg": wait_for_quiet()}

    # 1. Порог паузы.
    base = {"threshold": 0.5, "min_speech_duration_ms": 250, "max_speech_duration_s": 20, "speech_pad_ms": 100}
    pause_table = []
    print("1. Порог паузы: число Реплик (ожидаем 3 фразы) при разных min_silence_duration_ms")
    print("пауза в образце | " + " | ".join(f"{ms:>5}" for ms in (100, 200, 300, 400, 500, 700, 1000)))
    for pause_ms in (300, 500, 700, 1000, 1500):
        x = read_wav(SAMPLES_DIR / f"ppp_{pause_ms:04d}ms.wav")
        row = {"pause_ms": pause_ms, "segments": {}}
        for ms in (100, 200, 300, 400, 500, 700, 1000):
            segs = segments(vad, x, **base, min_silence_duration_ms=ms)
            row["segments"][ms] = [(round(s, 2), round(e, 2)) for s, e in segs]
        pause_table.append(row)
        print(f"{pause_ms:>14} | " + " | ".join(f"{len(row['segments'][ms]):>5}" for ms in (100, 200, 300, 400, 500, 700, 1000)))
    report["pause_threshold"] = pause_table
    # Внутренние разрывы фраз: сколько Реплик даёт детектор на одной непрерывной фразе (без пауз).
    for ms in (100, 200, 300, 500):
        x = read_wav(SAMPLES_DIR / "utt_10s.wav")
        print(f"   одна фраза 10 с, min_silence={ms} мс → Реплик: {len(segments(vad, x, **base, min_silence_duration_ms=ms))}")

    # 2. Принудительный разрез.
    print("\n2. Принудительный разрез непрерывной речи 48 с (max_speech_duration_s=20)")
    x = read_wav(SAMPLES_DIR / "speech_45s.wav")
    ref = manifest["speech_45s.wav"]["text"]
    forced = {}
    for min_sil in (300, 500):
        opts = {**base, "min_silence_duration_ms": min_sil}
        segs = segments(vad, x, **opts)
        t0 = time.perf_counter()
        res = list(model.with_vad(vad, **opts).recognize(x, sample_rate=SAMPLE_RATE))
        dt = time.perf_counter() - t0
        text = " ".join(r.text for r in res)
        forced[min_sil] = {
            "segments": [(round(s, 2), round(e, 2)) for s, e in segs],
            "lengths_s": [round(e - s, 2) for s, e in segs],
            "wer": round(wer(ref, text), 3),
            "recognize_s": round(dt, 2),
            "texts": [r.text for r in res],
        }
        print(f"   min_silence={min_sil} мс: отрезки {forced[min_sil]['lengths_s']} с, WER {forced[min_sil]['wer']:.3f}, время {dt:.2f} с")
    # Для сравнения — целиком (48 с > предела 25 с модели).
    t0 = time.perf_counter()
    whole = model.recognize(x, sample_rate=SAMPLE_RATE)
    print(f"   целиком 48 с (сверх предела): WER {wer(ref, whole):.3f}, время {time.perf_counter() - t0:.2f} с, слов {len(whole.split())} из {len(ref.split())}")
    forced["whole_48s"] = {"wer": round(wer(ref, whole), 3), "text": whole}
    report["forced_cut"] = forced

    # 3. Качество на окнах.
    print("\n3. Качество Реплики 20 с: целиком против окон")
    windows = {}
    for name in ("utt_20s_a.wav", "utt_20s_b.wav"):
        x = read_wav(SAMPLES_DIR / name)
        ref = manifest[name]["text"]
        rows = {}
        rows["whole"] = model.recognize(x, sample_rate=SAMPLE_RATE)
        for max_s in (10, 5):
            opts = {**base, "min_silence_duration_ms": 100, "max_speech_duration_s": max_s}
            res = list(model.with_vad(vad, **opts).recognize(x, sample_rate=SAMPLE_RATE))
            rows[f"vad_le_{max_s}s"] = " ".join(r.text for r in res)
            rows[f"vad_le_{max_s}s_lengths"] = [round(r.end - r.start, 2) for r in res]
        for win in (10, 5, 2.5):
            rows[f"fixed_{win}s"] = " ".join(model.recognize(fixed_windows(x, win), sample_rate=SAMPLE_RATE))
        windows[name] = {k: {"text": v, "wer": round(wer(ref, v), 3)} if isinstance(v, str) else v for k, v in rows.items()}
        for k, v in windows[name].items():
            if isinstance(v, dict):
                print(f"   {name} {k:>12}: WER {v['wer']:.3f}")
    report["windows"] = windows

    # 4. Пунктуация и числа.
    print("\n4. Пунктуация и числа")
    numbers = []
    for name in sorted(n for n in manifest if n.startswith("num_") or n.startswith("synth_")):
        x = read_wav(SAMPLES_DIR / name)
        text = model.recognize(x, sample_rate=SAMPLE_RATE)
        digits = re.findall(r"\d+", text)
        punct = len(re.findall(r"[.,!?;:—«»]", text))
        numbers.append({"sample": name, "ref": manifest[name]["text"], "text": text, "digits": digits, "punct_marks": punct})
        print(f"   {name}: цифры {digits}, знаков {punct}\n      эталон: {manifest[name]['text']}\n      вывод:  {text}")
    report["numbers"] = numbers

    # 5. Скорость детектора.
    x = read_wav(SAMPLES_DIR / "stream_a_300s.wav")
    t0 = time.perf_counter()
    segs = segments(vad, x, **base, min_silence_duration_ms=500)
    dt = time.perf_counter() - t0
    report["vad_speed"] = {"audio_s": round(duration_s(x), 1), "time_s": round(dt, 2), "segments": len(segs),
                           "lengths_s": [round(e - s, 1) for s, e in segs]}
    print(f"\n5. Детектор на 300 с: {dt:.2f} с, Реплик {len(segs)}, длины {report['vad_speed']['lengths_s']}")

    save_json(RESULTS_DIR / "vad_tune.json", report)


if __name__ == "__main__":
    main()
