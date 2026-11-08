"""Сводит JSON-итоги из ~/.local/share/spook/bench/results в таблицы Markdown (для отчёта).

Запуск:
    uv run --no-project python summarize.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from benchlib import RESULTS_DIR, load_json  # noqa: E402


def main() -> None:
    runs = []
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name == "vad_tune.json":
            continue
        runs.append((p.name, load_json(p)))

    print("## Загрузка и память\n")
    print("| библиотека | модель | потоков | загрузка, с | RSS после загрузки, МБ | пик RSS, МБ | первый вызов (5 с), с | нагрузка |")
    print("|---|---|---|---|---|---|---|---|")
    for name, r in runs:
        if "single" not in r:
            continue
        ld = r["load"]
        print(f"| {r['lib']} | {r['quant']} | {r['threads']} | {ld['load_s']:.2f} | {ld['rss_after_load_mb']} | {r['peak_rss_mb']} | {ld['first_call_s']:.2f} | {ld['loadavg']} |")

    print("\n## Одна Реплика, один поток распознавания (медиана из 3)\n")
    print("| библиотека | модель | потоков | 5 с | 10 с | 20 с (A) | 20 с (B) | нагрузка (при 20 с) |")
    print("|---|---|---|---|---|---|---|---|")
    for name, r in runs:
        if "single" not in r:
            continue
        s = {row["sample"]: row for row in r["single"]}
        cell = lambda k: f"{s[k]['median_s']:.2f} с (×{s[k]['audio_s'] / s[k]['median_s']:.0f})"  # noqa: E731
        print(f"| {r['lib']} | {r['quant']} | {r['threads']} | {cell('utt_05s.wav')} | {cell('utt_10s.wav')} | {cell('utt_20s_a.wav')} | {cell('utt_20s_b.wav')} | {s['utt_20s_a.wav']['loadavg']} |")

    print("\n## Две одновременные Реплики по 20 с — время до готовности обеих (медиана из 3)\n")
    print("| библиотека | модель | потоков | два потока, одна модель | одна очередь, по одной (первая) | батч из двух | два процесса | нагрузка |")
    print("|---|---|---|---|---|---|---|---|")
    for name, r in runs:
        c = r.get("concurrent")
        if not c:
            continue
        two = c["two_threads_one_model"]
        seq = c["sequential_one_queue"]
        batch = c["batch_of_two"]
        procs = c.get("two_processes")
        pcell = f"{procs['wall_median_s']:.2f} с" if procs else "—"
        print(f"| {r['lib']} | {r['quant']} | {r['threads']} | {two['wall_median_s']:.2f} с | {seq['wall_median_s']:.2f} с ({seq['first_done_median_s']:.2f}) | {batch['wall_median_s']:.2f} с | {pcell} | {two['loadavg']} / {seq['loadavg']} |")

    print("\n## Пять минут реального времени, два Источника, отрезки по 20 с\n")
    print("| библиотека | модель | потоков | обработчиков | отрезков | задержка: медиана | макс | макс в 1-й минуте | макс в последней минуте | макс ожидание в очереди | нагрузка начало → конец |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, r in runs:
        st = r.get("stream")
        if not st:
            continue
        print(f"| {r['lib']} | {r['quant']} | {r['threads']} | {st['workers']} | {st['chunks']} | {st['latency_median_s']:.2f} с | {st['latency_max_s']:.2f} с | {st['latency_first_minute_max_s']:.2f} с | {st['latency_last_minute_max_s']:.2f} с | {st['wait_max_s']:.2f} с | {st['loadavg_start']} → {st['loadavg_end_1min']} |")


if __name__ == "__main__":
    main()
