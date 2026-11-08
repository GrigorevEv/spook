"""Готовит звуковые образцы для замера (вне репозитория, в ~/.local/share/spook/bench/samples).

Источники:
- Russian LibriSpeech (RuLS, OpenSLR 96) — аудиокниги LibriVox, общественное достояние;
  зеркало на Hugging Face `bond005/rulibrispeech`, отдельные wav отдаёт datasets-server
  (16 кГц, моно, PCM16, с текстом). Берём прозу из train: строки 0–299 (Достоевский,
  «Записки из подполья», «Белые ночи»; читает один чтец) и 20000–20199 (Гончаров, «Обрыв»).
- Синтез Piper TTS, голос ru_RU-irina-medium (22,05 кГц → 16 кГц) — фраза с числами.

Запуск:
    uv run --no-project --with numpy --with soxr --with piper-tts python prepare_samples.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from benchlib import BENCH_DIR, SAMPLE_RATE, SAMPLES_DIR, duration_s, read_wav, save_json, write_wav  # noqa: E402

RAW_DIR = BENCH_DIR / "raw"
WAV_DIR = RAW_DIR / "wav"
PIPER_DIR = BENCH_DIR / "piper"
DATASET = "bond005/rulibrispeech"
ROWS_URL = "https://datasets-server.huggingface.co/rows?dataset={ds}&config=default&split=train&offset={off}&length=100"

READER_A = range(0, 300)  # Достоевский
READER_B = range(20000, 20200)  # Гончаров
NUMBER_ROWS = [47, 52, 105, 156, 198, 248]  # прозаические фразы с числительными

NUMBERS_TEXT = (
    "В две тысячи двадцать четвёртом году выручка выросла на пятнадцать процентов "
    "и достигла трёх миллионов двухсот пятидесяти тысяч рублей. "
    "Следующая встреча четырнадцатого марта в десять тридцать, участников будет семь."
)


def fetch_json(url: str, cache: Path, fresh: bool = False) -> dict:
    """Страница строк датасета; кэш годится только для текстов — ссылки на wav подписаны и истекают."""
    if cache.exists() and not fresh:
        try:
            data = json.loads(cache.read_text())
            if "rows" in data:
                return data
        except json.JSONDecodeError:
            pass
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                raw = r.read()
            data = json.loads(raw)
            if "rows" in data:
                cache.write_bytes(raw)
                return data
            print(f"ответ без rows ({attempt + 1}/5): {str(data)[:120]}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"ошибка загрузки ({attempt + 1}/5): {e}", file=sys.stderr)
    raise RuntimeError(f"не удалось получить {url}")


def fetch_rows(indices: range, fresh: bool = False) -> dict[int, dict]:
    """Строки датасета {row_idx: {"text": ..., "src": url}} для диапазона индексов."""
    rows: dict[int, dict] = {}
    for off in range(indices.start, indices.stop, 100):
        data = fetch_json(ROWS_URL.format(ds=DATASET, off=off), RAW_DIR / f"train_{off}.json", fresh)
        for r in data["rows"]:
            idx = r["row_idx"]
            if idx in indices:
                rows[idx] = {"text": r["row"]["transcription"], "src": r["row"]["audio"][0]["src"]}
    return rows


def download_wavs(indices: range) -> None:
    """Скачивает недостающие wav; ссылки берёт свежие, страницами по 100 строк."""
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    for off in range(indices.start, indices.stop, 100):
        page = range(off, min(off + 100, indices.stop))
        missing = [idx for idx in page if not (WAV_DIR / f"train_{idx:05d}.wav").exists()]
        if not missing:
            continue
        rows = fetch_rows(page, fresh=True)
        for idx in missing:
            path = WAV_DIR / f"train_{idx:05d}.wav"
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(rows[idx]["src"], timeout=120) as r:
                        path.write_bytes(r.read())
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"{path.name}: ошибка ({attempt + 1}/3): {e}", file=sys.stderr)
            else:
                raise RuntimeError(f"не скачан {path}")
    print(f"wav в {WAV_DIR}: {len(list(WAV_DIR.glob('*.wav')))}", file=sys.stderr)


def clip(idx: int) -> np.ndarray:
    return read_wav(WAV_DIR / f"train_{idx:05d}.wav")


def trim_silence(x: np.ndarray, threshold: float = 0.01, margin_s: float = 0.08) -> np.ndarray:
    """Обрезает тишину по краям (огибающая по окнам 20 мс ниже threshold), оставляя margin_s."""
    win = SAMPLE_RATE // 50
    n = len(x) // win
    env = np.abs(x[: n * win]).reshape(n, win).max(axis=1)
    loud = np.flatnonzero(env > threshold)
    if len(loud) == 0:
        return x
    margin = int(margin_s * SAMPLE_RATE)
    start = max(loud[0] * win - margin, 0)
    end = min((loud[-1] + 1) * win + margin, len(x))
    return x[start:end]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def glue(rows: dict[int, dict], order: list[int], target_s: float, gap_s: float, rng: random.Random | None = None,
         gap_jitter_s: float = 0.0, max_s: float | None = None) -> tuple[np.ndarray, list[dict]]:
    """Склеивает обрезанные клипы подряд, пока длительность не достигнет target_s.

    Если задан max_s, клипы, с которыми длительность превысит max_s, пропускаются.
    Возвращает звук и список частей {row, start, end, text}.
    """
    parts: list[np.ndarray] = []
    meta: list[dict] = []
    total = 0.0
    for idx in order:
        c = trim_silence(clip(idx))
        g = (gap_s + (rng.uniform(0, gap_jitter_s) if rng else 0.0)) if parts else 0.0
        if max_s is not None and total + g + duration_s(c) > max_s:
            if total >= target_s:
                break
            continue
        if parts:
            parts.append(silence(g))
            total += g
        meta.append({"row": idx, "start": round(total, 3), "end": round(total + duration_s(c), 3), "text": rows[idx]["text"]})
        parts.append(c)
        total += duration_s(c)
        if total >= target_s:
            break
    return np.concatenate(parts), meta


def closest_single(rows: dict[int, dict], candidates: range, target_s: float, tol_s: float) -> int | None:
    best, best_d = None, None
    for idx in candidates:
        d = abs(duration_s(clip(idx)) - target_s)
        if d <= tol_s and (best_d is None or d < best_d):
            best, best_d = idx, d
    return best


def synth_numbers() -> Path | None:
    """Синтез фразы с числами голосом Piper; None, если piper недоступен."""
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    voice = PIPER_DIR / "ru_RU-irina-medium.onnx"
    if not voice.exists():
        r = subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(PIPER_DIR),
                            "ru_RU-irina-medium"], capture_output=True, text=True)
        if r.returncode != 0:
            print("piper: не скачан голос:", r.stderr[-300:], file=sys.stderr)
            return None
    txt = PIPER_DIR / "numbers.txt"
    txt.write_text(NUMBERS_TEXT + "\n", encoding="utf-8")
    out22 = PIPER_DIR / "synth_numbers_22k.wav"
    r = subprocess.run([sys.executable, "-m", "piper", "-m", str(voice), "-i", str(txt), "-f", str(out22)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("piper: синтез не удался:", r.stderr[-300:], file=sys.stderr)
        return None
    import wave

    import soxr

    with wave.open(str(out22), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    y = soxr.resample(x, sr, SAMPLE_RATE).astype(np.float32)
    out = SAMPLES_DIR / "synth_numbers.wav"
    write_wav(out, y)
    return out


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    rows = fetch_rows(READER_A) | fetch_rows(READER_B)
    download_wavs(READER_A)
    download_wavs(READER_B)

    manifest: dict[str, dict] = {}

    def put(name: str, audio: np.ndarray, text: str, source: object) -> None:
        write_wav(SAMPLES_DIR / name, audio)
        manifest[name] = {"duration_s": round(duration_s(audio), 2), "text": text, "source": source}
        print(f"{name}: {duration_s(audio):.2f} с", file=sys.stderr)

    # Реплики 5 и 10 с — одиночные естественные клипы чтеца A, ближайшие по длине.
    for target, name in ((5.0, "utt_05s.wav"), (10.0, "utt_10s.wav")):
        idx = closest_single(rows, READER_A, target, 0.6)
        assert idx is not None, f"нет клипа около {target} с"
        put(name, clip(idx), rows[idx]["text"], {"rows": [idx]})

    # Реплики 20 с: одиночный клип, если есть, иначе склейка подряд идущих фраз без пауз.
    for reader, name, start in ((READER_A, "utt_20s_a.wav", 100), (READER_B, "utt_20s_b.wav", 20050)):
        idx = closest_single(rows, reader, 20.0, 1.0)
        if idx is not None:
            put(name, clip(idx), rows[idx]["text"], {"rows": [idx]})
        else:
            audio, meta = glue(rows, list(range(start, reader.stop)), 19.5, 0.1, max_s=21.0)
            put(name, audio, " ".join(m["text"] for m in meta), {"glued": meta, "gap_s": 0.1})

    # Непрерывная речь ≥ 40 с без пауз (склейка с зазором 0,1 с) для принудительного разреза.
    audio, meta = glue(rows, list(range(200, 300)), 45.0, 0.1)
    put("speech_45s.wav", audio, " ".join(m["text"] for m in meta), {"glued": meta, "gap_s": 0.1})

    # «Фраза — пауза — фраза» с разными паузами для подбора порога детектора.
    ppp_rows = [idx for idx in range(0, 60) if 3.0 <= duration_s(trim_silence(clip(idx))) <= 7.0][:3]
    for pause_ms in (300, 500, 700, 1000, 1500):
        parts = [silence(0.3)]
        meta = []
        t = 0.3
        for i, idx in enumerate(ppp_rows):
            if i:
                parts.append(silence(pause_ms / 1000))
                t += pause_ms / 1000
            c = trim_silence(clip(idx))
            meta.append({"row": idx, "start": round(t, 3), "end": round(t + duration_s(c), 3), "text": rows[idx]["text"]})
            parts.append(c)
            t += duration_s(c)
        parts.append(silence(0.3))
        put(f"ppp_{pause_ms:04d}ms.wav", np.concatenate(parts), " ".join(m["text"] for m in meta),
            {"phrases": meta, "pause_ms": pause_ms})

    # Два потока по 300 с: фразы подряд с естественными зазорами 0,3–0,7 с (разные чтецы).
    rng = random.Random(1)
    for reader, name in ((READER_A, "stream_a_300s.wav"), (READER_B, "stream_b_300s.wav")):
        audio, meta = glue(rows, list(reader), 300.0, 0.3, rng, 0.4)
        put(name, audio[: 300 * SAMPLE_RATE], " ".join(m["text"] for m in meta), {"glued": meta})

    # Числа: прозаические фразы с числительными (RuLS) и синтез Piper.
    for idx in NUMBER_ROWS:
        put(f"num_{idx:05d}.wav", clip(idx), rows[idx]["text"], {"rows": [idx]})
    synth = synth_numbers()
    if synth is not None:
        manifest[synth.name] = {"duration_s": round(duration_s(read_wav(synth)), 2), "text": NUMBERS_TEXT,
                                "source": {"piper": "ru_RU-irina-medium"}}

    save_json(SAMPLES_DIR / "samples.json", manifest)


if __name__ == "__main__":
    main()
