from __future__ import annotations

import sys
import tarfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from benchlib import MODELS_DIR  # noqa: E402

ONNX_ASR_DIR = MODELS_DIR / "onnx-asr" / "gigaam-v3-e2e-rnnt"
VAD_DIR = MODELS_DIR / "onnx-asr" / "silero-vad"
SHERPA_NAME = "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"
SHERPA_DIR = MODELS_DIR / "sherpa-onnx"
SHERPA_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SHERPA_NAME}.tar.bz2"


def fetch_onnx_asr(fp32: bool) -> None:
    import onnx_asr

    t0 = time.perf_counter()
    onnx_asr.load_model("gigaam-v3-e2e-rnnt", ONNX_ASR_DIR, quantization="int8")
    print(f"onnx-asr int8 → {ONNX_ASR_DIR} ({time.perf_counter() - t0:.0f} с, включая загрузку в память)")
    if fp32:
        t0 = time.perf_counter()
        _fetch_fp32_into_existing_dir()
        onnx_asr.load_model("gigaam-v3-e2e-rnnt", ONNX_ASR_DIR)
        print(f"onnx-asr fp32 → {ONNX_ASR_DIR} ({time.perf_counter() - t0:.0f} с)")
    onnx_asr.load_vad("silero", VAD_DIR)
    print(f"silero vad → {VAD_DIR}")
    for p in sorted(ONNX_ASR_DIR.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(MODELS_DIR)}  {p.stat().st_size / 1024 / 1024:.1f} МБ")


def _fetch_fp32_into_existing_dir() -> None:
    from huggingface_hub import hf_hub_download

    for name in ("v3_e2e_rnnt_encoder.onnx", "v3_e2e_rnnt_decoder.onnx", "v3_e2e_rnnt_joint.onnx"):
        hf_hub_download("istupakov/gigaam-v3-onnx", name, local_dir=ONNX_ASR_DIR)


def fetch_sherpa() -> None:
    SHERPA_DIR.mkdir(parents=True, exist_ok=True)
    target = SHERPA_DIR / SHERPA_NAME
    if target.exists():
        print(f"sherpa-onnx: уже есть {target}")
    else:
        archive = SHERPA_DIR / f"{SHERPA_NAME}.tar.bz2"
        if not archive.exists():
            print(f"sherpa-onnx: скачиваю {SHERPA_URL}")
            urllib.request.urlretrieve(SHERPA_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(SHERPA_DIR, filter="data")
        archive.unlink()
    for p in sorted(target.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(MODELS_DIR)}  {p.stat().st_size / 1024 / 1024:.1f} МБ")


if __name__ == "__main__":
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    fetch_onnx_asr(fp32="--fp32" in sys.argv)
    if "--no-sherpa" not in sys.argv:
        fetch_sherpa()
