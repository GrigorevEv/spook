# Замер скорости GigaAM-v3 при двух Источниках

Скрипты одноразовые, но воспроизводимые; запускаются из этого каталога через `uv run --with …` (venv проекта не нужен). Модели и звук лежат **вне репозитория** — в `~/.local/share/spook/bench/` (каталог меняется переменной `SPOOK_BENCH_DIR`): `models/`, `samples/`, `raw/`, `piper/`, `results/`. Итоги — `docs/research/gigaam-benchmark.md`. `prepare_samples.py` нужен и медленным тестам: они читают образцы из `samples/`.

## Порядок воспроизведения

```sh
cd scripts/gigaam-bench

# 1. Образцы речи (RuLS через HF datasets-server + синтез Piper): 500 исходных wav ≈ 95 МБ, голос Piper 60 МБ, ~10 мин.
uv run --no-project --with numpy --with soxr --with piper-tts python prepare_samples.py

# 2. Модели: onnx-asr (int8 ≈ 215 МБ, --fp32 добавляет 850 МБ) и архив sherpa-onnx (≈ 220 МБ).
uv run --no-project --with "onnx-asr[cpu,hub]" python fetch_models.py --fp32

# 3. Перебор числа потоков onnxruntime: загрузка, Реплики 5/10/20 с, две одновременные по 20 с.
./run_sweep.sh onnx-asr "1 2 4 6 8 10"
./run_sweep.sh sherpa "1 2 4 6"
./run_sweep.sh onnx-asr "4" --quant fp32

# 4. Два процесса с двумя моделями (в перебор не входит из экономии времени).
uv run --no-project --with "onnx-asr[cpu,hub]" --with numpy python -u bench.py --lib onnx-asr --threads 4 --stages load,single,concurrent

# 5. Пять минут реального времени, два Источника, нарезка по 20 с, одна очередь.
uv run --no-project --with "onnx-asr[cpu,hub]" --with numpy python -u bench.py --lib onnx-asr --threads 4 --stages load,stream --workers 1 --tag _stream
uv run --no-project --with sherpa-onnx --with numpy python -u bench.py --lib sherpa --threads 4 --stages load,stream --workers 1 --tag _stream

# 6. Детектор речи: порог паузы, принудительный разрез, качество на окнах, пунктуация и числа.
uv run --no-project --with "onnx-asr[cpu,hub]" --with numpy python -u vad_tune.py

# 7. Таблицы Markdown из JSON-итогов.
uv run --no-project python summarize.py
```

## Файлы

- `benchlib.py` — общее: каталоги, ожидание тишины по `/proc/loadavg` (минутная нагрузка ≤ 6, иначе до 10 раз по 60 с), RSS из `/proc/self`, чтение/запись wav, WER, медиана.
- `prepare_samples.py` — образцы: что откуда берётся, описано в начале файла; манифест с эталонными текстами — `samples/samples.json`.
- `fetch_models.py` — загрузка моделей в `models/`.
- `bench.py` — замер (`--lib onnx-asr|sherpa`, `--threads`, `--quant int8|fp32`, `--stages load,single,concurrent,stream`, `--workers`). Итог — `results/<lib>_<quant>_t<threads><tag>.json` с нагрузкой рядом с каждым числом.
- `run_sweep.sh` — перебор числа потоков для одной библиотеки.
- `sherpa_check.py` — быстрая проверка, что sherpa-onnx даёт тот же текст.
- `vad_tune.py` — подбор детектора; итог `results/vad_tune.json`.
- `summarize.py` — таблицы для отчёта.
- Копии JSON-итогов и журналов прогонов, на которых построен отчёт, — в архиве истории (см. `AGENTS.md`), тег `tran-v1`: `git show tran-v1:.scratch/tran-v1/artifacts/01-gigaam-speed-measurement/results/<файл>`.

## Образцы

| файл | длительность | что это |
|---|---|---|
| `utt_05s.wav`, `utt_10s.wav` | 5,0 / 10,0 с | одиночные фразы чтеца A (RuLS train 0–299, Достоевский) |
| `utt_20s_a.wav`, `utt_20s_b.wav` | 20,8 / 21,0 с | склейка подряд идущих фраз без пауз (зазор 0,1 с): чтец A и чтец B (RuLS train 20000–20199, Гончаров) |
| `speech_45s.wav` | 48,3 с | непрерывная речь без пауз для принудительного разреза |
| `ppp_0300ms…ppp_1500ms.wav` | 16,6–19,0 с | «фраза — пауза — фраза — пауза — фраза» с паузами 300, 500, 700, 1000, 1500 мс |
| `stream_a_300s.wav`, `stream_b_300s.wav` | 300 с | два Источника: фразы подряд с зазорами 0,3–0,7 с, два чтеца |
| `num_*.wav` | 4–17 с | прозаические фразы RuLS с числительными («до шестидесяти лет», «шесть тысяч рублей», «в десять часов» …) |
| `synth_numbers.wav` | 13,7 с | синтез Piper `ru_RU-irina-medium`: год, проценты, сумма, дата, время |

Russian LibriSpeech (RuLS, OpenSLR 96) — аудиокниги LibriVox, общественное достояние; зеркало `bond005/rulibrispeech` на Hugging Face. Все образцы — 16 кГц, моно, PCM16.
