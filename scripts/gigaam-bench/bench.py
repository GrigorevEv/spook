"""Замер GigaAM-v3 e2e_rnnt на CPU: загрузка, память, скорость, две одновременные Реплики, 5 минут потока.

Запуск (onnx-asr):
    uv run --no-project --with "onnx-asr[cpu,hub]" --with numpy python bench.py --lib onnx-asr --threads 4 \
        --stages load,single,concurrent
Запуск (sherpa-onnx):
    uv run --no-project --with sherpa-onnx --with numpy python bench.py --lib sherpa --threads 4 --stages load,single

Этапы (--stages): load — время загрузки и RSS; single — Реплики 5/10/20 с по одной; concurrent — две
Реплики по 20 с одновременно (два потока на одной модели; последовательно одной очередью; два процесса,
каждый со своей моделью), по 3 повтора, медиана; stream — 5 минут реального времени, два Источника,
нарезка по 20 с, одна очередь (--workers 1) — отставание не должно расти.

Итог — JSON в ~/.local/share/spook/bench/results/<lib>_<quant>_t<threads>.json и таблица в stdout.
Перед каждым замером проверяется /proc/loadavg (минутная нагрузка ≤ 6, иначе ожидание до 10×60 с);
нагрузка записывается рядом с каждым числом.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from benchlib import (  # noqa: E402
    MODELS_DIR,
    RESULTS_DIR,
    SAMPLE_RATE,
    SAMPLES_DIR,
    duration_s,
    load_json,
    median,
    peak_rss_mb,
    read_wav,
    rss_mb,
    save_json,
    wait_for_quiet,
    wer,
)

ONNX_ASR_DIR = MODELS_DIR / "onnx-asr" / "gigaam-v3-e2e-rnnt"
SHERPA_DIR = MODELS_DIR / "sherpa-onnx" / "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"


# --- адаптеры библиотек -------------------------------------------------------------------------


class OnnxAsrEngine:
    name = "onnx-asr"

    def __init__(self, threads: int, quant: str | None):
        import onnx_asr
        import onnxruntime as rt

        so = rt.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.model = onnx_asr.load_model("gigaam-v3-e2e-rnnt", ONNX_ASR_DIR, quantization=quant, sess_options=so)
        self.threads_note = f"intra_op_num_threads={threads}, inter_op_num_threads=1"

    def recognize(self, samples: np.ndarray) -> str:
        return self.model.recognize(samples, sample_rate=SAMPLE_RATE)

    def recognize_batch(self, items: list[np.ndarray]) -> list[str]:
        return self.model.recognize(items, sample_rate=SAMPLE_RATE)


class SherpaEngine:
    name = "sherpa"

    def __init__(self, threads: int, quant: str | None):
        import sherpa_onnx

        encoder = SHERPA_DIR / ("encoder.int8.onnx" if quant == "int8" else "encoder.onnx")
        if not encoder.exists():
            raise SystemExit(f"нет файла {encoder} (в архиве sherpa-onnx только int8-энкодер)")
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(encoder),
            decoder=str(SHERPA_DIR / "decoder.onnx"),
            joiner=str(SHERPA_DIR / "joiner.onnx"),
            tokens=str(SHERPA_DIR / "tokens.txt"),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=64,
            decoding_method="greedy_search",
            model_type="nemo_transducer",
        )
        self.threads_note = f"num_threads={threads}"

    def recognize(self, samples: np.ndarray) -> str:
        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, samples)
        self.rec.decode_stream(s)
        return s.result.text

    def recognize_batch(self, items: list[np.ndarray]) -> list[str]:
        streams = []
        for x in items:
            s = self.rec.create_stream()
            s.accept_waveform(SAMPLE_RATE, x)
            streams.append(s)
        self.rec.decode_streams(streams)
        return [s.result.text for s in streams]


ENGINES = {"onnx-asr": OnnxAsrEngine, "sherpa": SherpaEngine}


# --- этапы --------------------------------------------------------------------------------------


def timed(fn, *args):
    t0 = time.perf_counter()
    out = fn(*args)
    return time.perf_counter() - t0, out


def stage_load(args) -> tuple[object, dict]:
    rss0 = rss_mb()
    load = wait_for_quiet()
    t_load, engine = timed(ENGINES[args.lib], args.threads, args.quant)
    rss1 = rss_mb()
    warm = read_wav(SAMPLES_DIR / "utt_05s.wav")
    t_first, text = timed(engine.recognize, warm)
    rss2 = rss_mb()
    res = {
        "loadavg": load,
        "load_s": round(t_load, 2),
        "rss_before_mb": round(rss0),
        "rss_after_load_mb": round(rss1),
        "rss_after_first_mb": round(rss2),
        "first_call_s": round(t_first, 2),
        "first_call_text": text,
        "threads": engine.threads_note,
    }
    print(f"[load] {t_load:.2f} с, RSS {rss0:.0f} → {rss1:.0f} → {rss2:.0f} МБ, первый вызов {t_first:.2f} с, нагрузка {load}")
    return engine, res


def stage_single(engine, manifest: dict, repeats: int) -> list[dict]:
    out = []
    for name in ("utt_05s.wav", "utt_10s.wav", "utt_20s_a.wav", "utt_20s_b.wav"):
        x = read_wav(SAMPLES_DIR / name)
        load = wait_for_quiet()
        times, text = [], ""
        for _ in range(repeats):
            t, text = timed(engine.recognize, x)
            times.append(t)
        d = duration_s(x)
        med = median(times)
        row = {
            "sample": name,
            "audio_s": round(d, 2),
            "times_s": [round(t, 3) for t in times],
            "median_s": round(med, 3),
            "rtf": round(med / d, 4),
            "loadavg": load,
            "text": text,
            "wer": round(wer(manifest[name]["text"], text), 3),
        }
        out.append(row)
        print(f"[single] {name} {d:.1f} с: медиана {med:.2f} с (×{d / med:.1f} реального времени), WER {row['wer']:.3f}, нагрузка {load}")
    return out


def _run_pair_threads(engine, a, b) -> tuple[float, float, float, list[str]]:
    results = [None, None]
    times = [0.0, 0.0]

    def work(i, x):
        t0 = time.perf_counter()
        results[i] = engine.recognize(x)
        times[i] = time.perf_counter() - t0

    start = threading.Barrier(3)

    def runner(i, x):
        start.wait()
        work(i, x)

    threads = [threading.Thread(target=runner, args=(0, a)), threading.Thread(target=runner, args=(1, b))]
    for t in threads:
        t.start()
    start.wait()
    t0 = time.perf_counter()
    for t in threads:
        t.join()
    return time.perf_counter() - t0, times[0], times[1], results


def _proc_worker(lib, threads, quant, sample, barrier, q):
    engine = ENGINES[lib](threads, quant)
    x = read_wav(SAMPLES_DIR / sample)
    engine.recognize(read_wav(SAMPLES_DIR / "utt_05s.wav"))  # прогрев
    barrier.wait()
    t0 = time.perf_counter()
    text = engine.recognize(x)
    q.put((sample, time.perf_counter() - t0, text, round(peak_rss_mb())))


def _run_pair_processes(args) -> dict:
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(3)
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_proc_worker, args=(args.lib, args.threads, args.quant, s, barrier, q))
        for s in ("utt_20s_a.wav", "utt_20s_b.wav")
    ]
    for p in procs:
        p.start()
    barrier.wait()
    t0 = time.perf_counter()
    got = [q.get(timeout=600), q.get(timeout=600)]
    wall = time.perf_counter() - t0
    for p in procs:
        p.join()
    return {"wall_s": round(wall, 3), "each_s": {s: round(t, 3) for s, t, _, _ in got}, "peak_rss_mb": {s: r for s, _, _, r in got}}


def stage_concurrent(engine, args, repeats: int) -> dict:
    a = read_wav(SAMPLES_DIR / "utt_20s_a.wav")
    b = read_wav(SAMPLES_DIR / "utt_20s_b.wav")
    res: dict = {"audio_s": [round(duration_s(a), 2), round(duration_s(b), 2)]}

    load = wait_for_quiet()
    walls, ea, eb = [], [], []
    for _ in range(repeats):
        w, ta, tb, _ = _run_pair_threads(engine, a, b)
        walls.append(w), ea.append(ta), eb.append(tb)
    res["two_threads_one_model"] = {
        "loadavg": load,
        "wall_s": [round(w, 3) for w in walls],
        "wall_median_s": round(median(walls), 3),
        "a_median_s": round(median(ea), 3),
        "b_median_s": round(median(eb), 3),
    }
    print(f"[concurrent] два потока, одна модель: обе готовы через {median(walls):.2f} с (медиана из {repeats}), нагрузка {load}")

    load = wait_for_quiet()
    walls, firsts = [], []
    for _ in range(repeats):
        t0 = time.perf_counter()
        engine.recognize(a)
        firsts.append(time.perf_counter() - t0)
        engine.recognize(b)
        walls.append(time.perf_counter() - t0)
    res["sequential_one_queue"] = {
        "loadavg": load,
        "wall_s": [round(w, 3) for w in walls],
        "wall_median_s": round(median(walls), 3),
        "first_done_median_s": round(median(firsts), 3),
    }
    print(f"[concurrent] одна очередь, по одной: обе готовы через {median(walls):.2f} с, первая через {median(firsts):.2f} с, нагрузка {load}")

    load = wait_for_quiet()
    walls = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        engine.recognize_batch([a, b])
        walls.append(time.perf_counter() - t0)
    res["batch_of_two"] = {"loadavg": load, "wall_s": [round(w, 3) for w in walls], "wall_median_s": round(median(walls), 3)}
    print(f"[concurrent] батч из двух: обе готовы через {median(walls):.2f} с, нагрузка {load}")

    if not args.no_processes:
        load = wait_for_quiet()
        runs = [_run_pair_processes(args) for _ in range(repeats)]
        res["two_processes"] = {
            "loadavg": load,
            "wall_s": [r["wall_s"] for r in runs],
            "wall_median_s": round(median([r["wall_s"] for r in runs]), 3),
            "peak_rss_mb": runs[0]["peak_rss_mb"],
        }
        print(f"[concurrent] два процесса, две модели: обе готовы через {res['two_processes']['wall_median_s']:.2f} с, нагрузка {load}")
    return res


def stage_stream(engine, args, manifest: dict) -> dict:
    """Два Источника в реальном времени: каждые 20 с приходят по одному 20-секундному отрезку от каждого."""
    chunk = args.chunk_s * SAMPLE_RATE
    streams = {"A": read_wav(SAMPLES_DIR / "stream_a_300s.wav"), "B": read_wav(SAMPLES_DIR / "stream_b_300s.wav")}
    n_chunks = min(len(x) for x in streams.values()) // chunk
    total_s = n_chunks * args.chunk_s
    q: queue.Queue = queue.Queue()
    done: list[dict] = []
    lock = threading.Lock()

    def worker():
        while True:
            item = q.get()
            if item is None:
                return
            src, i, arrival, x = item
            t_start = time.perf_counter()
            text = engine.recognize(x)
            t_end = time.perf_counter()
            with lock:
                done.append({
                    "src": src,
                    "chunk": i,
                    "arrival_s": round(arrival - t0, 3),
                    "wait_s": round(t_start - arrival, 3),
                    "recognize_s": round(t_end - t_start, 3),
                    "latency_s": round(t_end - arrival, 3),
                    "words": len(text.split()),
                })

    load_start = wait_for_quiet()
    workers = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    print(f"[stream] {total_s} с реального времени, нарезка {args.chunk_s} с, {args.workers} обработчик(а), нагрузка {load_start}")
    for i in range(n_chunks):
        due = t0 + (i + 1) * args.chunk_s
        while (now := time.perf_counter()) < due:
            time.sleep(min(0.05, due - now))
        arrival = time.perf_counter()
        if i % 3 == 2:
            # Состояние перед подачей очередной пары: всё ли предыдущее уже распознано.
            with lock:
                lat = [d["latency_s"] for d in done]
            print(f"  t={int(arrival - t0)} с: готово {len(lat)} из {2 * i}, не распознано {q.qsize()}, последняя задержка {lat[-1] if lat else '—'} с", flush=True)
        for src, x in streams.items():
            q.put((src, i, arrival, x[i * chunk : (i + 1) * chunk]))
    for _ in workers:
        q.put(None)
    for w in workers:
        w.join()
    load_end = f"{__import__('benchlib').loadavg()[0]:.2f}"
    done.sort(key=lambda d: (d["arrival_s"], d["src"]))
    lat = [d["latency_s"] for d in done]
    first_min = [d["latency_s"] for d in done if d["arrival_s"] <= 60]
    last_min = [d["latency_s"] for d in done if d["arrival_s"] > total_s - 60]
    res = {
        "loadavg_start": load_start,
        "loadavg_end_1min": load_end,
        "chunk_s": args.chunk_s,
        "workers": args.workers,
        "chunks": len(done),
        "latency_max_s": max(lat),
        "latency_median_s": round(median(lat), 3),
        "latency_first_minute_max_s": max(first_min),
        "latency_last_minute_max_s": max(last_min),
        "wait_max_s": max(d["wait_s"] for d in done),
        "recognize_median_s": round(median([d["recognize_s"] for d in done]), 3),
        "per_chunk": done,
    }
    print(f"[stream] задержка: макс {res['latency_max_s']:.2f} с, медиана {res['latency_median_s']:.2f} с; первая минута макс {res['latency_first_minute_max_s']:.2f}, последняя минута макс {res['latency_last_minute_max_s']:.2f}; нагрузка в конце {load_end}")
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--lib", choices=ENGINES, required=True)
    p.add_argument("--threads", type=int, default=4, help="потоки onnxruntime (intra_op / num_threads)")
    p.add_argument("--quant", default="int8", help="int8 или fp32")
    p.add_argument("--stages", default="load,single,concurrent")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--workers", type=int, default=1, help="обработчиков очереди на этапе stream")
    p.add_argument("--chunk-s", type=int, default=20)
    p.add_argument("--no-processes", action="store_true")
    p.add_argument("--tag", default="")
    args = p.parse_args()
    if args.quant == "fp32":
        args.quant = None

    manifest = load_json(SAMPLES_DIR / "samples.json")
    stages = args.stages.split(",")
    result = {"lib": args.lib, "quant": args.quant or "fp32", "threads": args.threads, "cpu": "i7-13620H"}

    engine, result["load"] = stage_load(args)
    if "single" in stages:
        result["single"] = stage_single(engine, manifest, args.repeats)
    if "concurrent" in stages:
        result["concurrent"] = stage_concurrent(engine, args, args.repeats)
    if "stream" in stages:
        result["stream"] = stage_stream(engine, args, manifest)
    result["peak_rss_mb"] = round(peak_rss_mb())
    print(f"[память] пиковый RSS процесса {result['peak_rss_mb']} МБ")

    out = RESULTS_DIR / f"{args.lib}_{result['quant']}_t{args.threads}{args.tag}.json"
    save_json(out, result)


if __name__ == "__main__":
    main()
