"""Exercise both narrator packs through the real /tts API on CPU, without binding a port."""

from pathlib import Path
import io
import json
import os
import secrets
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/narrator-auditions"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

import yaml
import torch

torch.set_num_threads(8)
torch.set_num_interop_threads(2)
torch.manual_seed(20261021)
source_config = ROOT / "mambo_cpu.yaml"
if not source_config.exists():
    source_config = ROOT / "GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml"
cfg = yaml.safe_load(source_config.read_text(encoding="utf-8"))
cfg.setdefault("custom", {}).update(
    {
        "device": "cpu",
        "is_half": False,
        "version": "v2ProPlus",
        "t2s_weights_path": str(ROOT / "data/voices/播音女声/gpt_weights.ckpt"),
        "vits_weights_path": str(ROOT / "data/voices/播音女声/sovits_weights.pth"),
    }
)
config_path = OUT / "narrators_cpu.yaml"
config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
os.chdir(ROOT)
admin_token = secrets.token_urlsafe(32)
os.environ.update(
    TTS_BACKEND="gsv",
    GSV_DEVICE="cpu",
    GSV_IS_HALF="false",
    TTS_WORKERS="2",
    GSV_CONFIG=str(config_path),
    TTS_DATA_DIR=str(ROOT / "data"),
    OMP_NUM_THREADS="8",
    MKL_NUM_THREADS="8",
    TTS_ADMIN_TOKEN=admin_token,
)

from fastapi.testclient import TestClient
from app.main import app
import numpy as np

TEXT = "大家好，欢迎收看本期科技观察。今天，我们介绍迷你热成像仪的测温功能，以及开源软件带来的全新玩法。"
report = []
with TestClient(app) as client:
    admin_headers = {"X-Admin-Token": admin_token}
    for name, slug in [("播音女声", "narrator_female"), ("播音男声", "narrator_male")]:
        start = time.monotonic()
        response = client.post(
            "/tts",
            headers=admin_headers,
            json={
                "text": TEXT,
                "voice": name,
                "text_lang": "zh",
                "speed_factor": 1.04,
                "top_k": 15,
                "top_p": 1.0,
                "temperature": 0.7,
                "max_sec": 40,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(response.text)
        path = OUT / f"{slug}_preview.wav"
        path.write_bytes(response.content)
        with wave.open(io.BytesIO(response.content)) as w:
            rate = w.getframerate()
            seconds = w.getnframes() / rate
            channels = w.getnchannels()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(
                np.float64
            )
        assert 4 < seconds < 39.9 and channels == 1
        assert np.max(np.abs(audio)) > 1000
        clipping = float(np.mean(np.abs(audio) >= 32767))
        assert clipping < 0.001
        rms_dbfs = float(20 * np.log10(np.sqrt(np.mean(audio**2)) / 32768))
        assert rms_dbfs > -28, "Voice output is too quiet for the narration preset"
        item = {
            "voice": name,
            "text": TEXT,
            "file": str(path.relative_to(ROOT)),
            "duration_seconds": round(seconds, 4),
            "sample_rate": rate,
            "channels": channels,
            "rms_dbfs": round(rms_dbfs, 2),
            "clip_sample_ratio": clipping,
            "wall_seconds": round(time.monotonic() - start, 2),
            "http_status": response.status_code,
            "device": "cpu",
            "backend": "gpt-sovits",
        }
        report.append(item)
        (OUT / "api_smoke.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(item, ensure_ascii=False), flush=True)
        import shutil

        shutil.copy2(path, ROOT / "data" / "voices" / name / "preview.wav")
    status = client.get("/api/status", headers=admin_headers).json()
    backend = status["engine"]["backend"]
    assert backend.get("device") == "cpu" and not backend.get("demo")
    (OUT / "backend_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
print("Both real CPU API calls completed.", flush=True)
