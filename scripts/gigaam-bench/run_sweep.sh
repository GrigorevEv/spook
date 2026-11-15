#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
lib="$1"; threads="$2"; shift 2
case "$lib" in
  onnx-asr) deps=(--with "onnx-asr[cpu,hub]" --with numpy) ;;
  sherpa)   deps=(--with sherpa-onnx --with numpy) ;;
  *) echo "неизвестная библиотека: $lib" >&2; exit 1 ;;
esac
for t in $threads; do
  echo "=== $lib, потоков: $t, loadavg: $(cat /proc/loadavg)"
  uv run --no-project "${deps[@]}" python -u bench.py --lib "$lib" --threads "$t" --stages load,single,concurrent --no-processes "$@" 2>&1 \
    | grep -v "Warning\|записано"
done
