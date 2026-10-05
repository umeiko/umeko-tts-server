# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

A text-to-speech service and admin console built on [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS). It exposes a `/tts` endpoint fully compatible with the Mambo TTS API, with multi-voice management, weight upload, concurrent queuing, and a built-in web console.

## Features

- **Mambo TTS API compatible**: `POST /tts` with the same parameters, defaults, and error format (`{"message": "..."}`) — drop-in replacement for existing clients
- **Multi-voice management**: create / edit / delete voices, upload GPT weights (.ckpt), SoVITS weights (.pth), and reference audio; Chinese voice names supported
- **Hot weight switching**: the model swaps automatically per requested voice; loaded weights are cached and reused, so switching costs nothing
- **Concurrency**: requests run in a thread pool (auto-sized from CPU cores + available memory); inference is guarded by a global serial lock — requests queue up cleanly, never race, and the event loop never blocks
- **Web console**: voice management, online audition, live queue status, API docs — pure static pages, no build step
- **Demo backend**: falls back to a sine-wave mock backend when GPT-SoVITS is not installed, so the API / console / concurrency behavior can be fully self-tested

## Quick Start (Demo Mode)

```bash
pip install -r requirements.txt
python -m app.main
```

Open the console: <http://127.0.0.1:9880/console/>

This runs the mock backend (sine-wave test audio) to verify the service and management features.

## Real Synthesis with GPT-SoVITS

```bash
# 1. Clone the repo (into the project root, or point GSV_ROOT elsewhere)
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS

# 2. Create a venv (Python 3.10/3.11 recommended; uv works great)
uv venv .venv --python 3.11

# 3. Install torch — pick ONE:
#    NVIDIA GPU (CUDA 12.4, e.g. RTX 30/40 series):
uv pip install --python .venv/Scripts/python.exe torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu124
#    CPU only:
uv pip install --python .venv/Scripts/python.exe torch torchaudio

# 4. Install GPT-SoVITS dependencies plus a few runtime extras:
#    transformers librosa soundfile onnxruntime langsegment jieba pypinyin
#    cn2an g2p_en fast_langdetect split-lang wordsegment ToJyutping g2pk2
#    ko_pron opencc-python-reimplemented pyopenjtalk-prebuilt sentencepiece
#    rotary_embedding_torch x_transformers ffmpeg-python pytorch-lightning
#    torchmetrics matplotlib peft
#    (see GPT-SoVITS/requirements.txt for the authoritative list)

# 5. Download pretrained models into GPT-SoVITS/GPT_SoVITS/pretrained_models/:
#    - chinese-roberta-wwm-ext-large/
#    - chinese-hubert-base/
#    - sv/pretrained_eres2netv2w24s4ep4.ckpt
#    - fast_langdetect/lid.176.bin
#    (all available from huggingface.co/lj1995/GPT-SoVITS or hf-mirror.com)

# 6. Start (forces the real backend; fails loudly if anything is missing)
TTS_BACKEND=gsv python -m app.main   # Linux / macOS
# Windows cmd:   set TTS_BACKEND=gsv && python -m app.main
# PowerShell:    $env:TTS_BACKEND="gsv"; python -m app.main
```

Notes:

- At startup the service chdirs into `GSV_ROOT` so the relative pretrained-model paths in the official `tts_infer.yaml` resolve; all data paths of this service are absolute and unaffected.
- **Windows**: torchaudio ≥ 2.9 defaults to torchcodec for `load()`, which needs full FFmpeg DLLs. This project monkey-patches `torchaudio.load` with a soundfile implementation (`app/backends/gsv.py`), so no FFmpeg install is required.
- **Low-memory CPU machines**: `scripts/quantize_pretrained.py` pre-quantizes the BERT/HuBERT pretrained models to int8 sidecar files; on CPU the backend loads them directly, avoiding the fp32 load-then-quantize memory spike.

## Configuration (Environment Variables)

| Variable | Default | Description |
|------|--------|------|
| `TTS_HOST` | `0.0.0.0` | Listen address |
| `TTS_PORT` | `9880` | Listen port |
| `TTS_BACKEND` | `auto` | `auto` (GSV if available, else mock) / `gsv` (force real) / `mock` (demo) |
| `TTS_DATA_DIR` | `./data` | Voice & registry storage directory |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS repo directory |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | Inference config |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
| `GSV_IS_HALF` | `auto` | `auto` (half precision on CUDA) / `true` / `false` |
| `TTS_WORKERS` | `0` (auto) | Thread pool size; 0 = CPU cores + 4, shrunk by free memory (cap 32) |
| `TTS_MAX_QUEUE` | `0` (unlimited) | Max queued requests; overflow gets 503 |
| `TTS_ADMIN_TOKEN` | empty | If set, `/api/*` requires the `X-Admin-Token` header |
| `TTS_CORS_ORIGINS` | `*` | Allowed CORS origins, comma-separated |

## TTS Endpoint

`POST /tts` · `Content-Type: application/json`

| Parameter | Type | Required | Default | Description |
|------|------|------|--------|------|
| text | string | yes | — | Text to synthesize; supports zh/en/ja/ko/yue |
| voice | string | no | default voice | Extension: voice name |
| text_lang | string | no | zh | zh / en / ja / ko / yue / auto |
| speed_factor | float | no | 1.0 | Speed multiplier, 0.5~2.0 (clamped) |
| top_k | int | no | 15 | GPT sampling top-k, 1~100 |
| top_p | float | no | 1.0 | Nucleus sampling threshold, 0~1 |
| temperature | float | no | 1.0 | Sampling temperature, 0.1~2.0 |
| cut_punc | string | no | "" | Manual split punctuation (e.g. `。！？`); empty = auto |
| max_sec | float | no | 10.0 | Max audio seconds, capped at 60 (truncated) |

- Success (200): `audio/wav` binary, with `X-Voice` (URL-encoded voice name) and `X-Inference-Ms` response headers
- Failure (4xx/5xx): `{"message": "error description"}`

```bash
# Minimal call
curl -X POST http://127.0.0.1:9880/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}' -o output.wav
```

## Admin API (/api/*)

| Method | Path | Description |
|------|------|------|
| GET | /api/status | Service status: backend, device, threads, queue counts, uptime |
| GET | /api/voices | Voice list |
| POST | /api/voices | Create voice (multipart: `name`*, `description`, `ref_text`, `ref_lang`, `gpt_file`, `sovits_file`, `ref_audio`) |
| PUT | /api/voices/{name} | Update voice (same fields, all optional; same-name files are replaced) |
| DELETE | /api/voices/{name} | Delete voice and all its files |
| POST | /api/voices/{name}/default | Set as default voice |
| GET | /api/voices/{name}/ref-audio | Download / audition reference audio |

A usable voice = GPT weights + SoVITS weights + reference audio (+ reference text/language). You can create a name-only voice first (status "incomplete") and upload files later in the console; the real backend requires all three files.

## Concurrency Model

```
request → FastAPI event loop → thread pool (loop never blocks)
                                └→ global serial lock → weight switch + inference (queued)
```

- Pool size auto-adapts to hardware; pin it with `TTS_WORKERS`
- The serial lock keeps single-GPU / CPU inference from interleaving; multi-voice requests serialize with automatic weight switching
- The console status bar refreshes queue counts every 3 seconds (pending / running / done / failed)

## Smoke Test

With the service running (the demo backend passes everything):

```bash
python scripts/smoke_test.py            # default http://127.0.0.1:9880
python scripts/smoke_test.py http://host:port
```

Covers: status, voice CRUD (incl. Chinese names and file upload), minimal / full / invalid params, error format, 8-way concurrency, max_sec truncation, console page.

## Directory Layout

```
umeko-tts-server/
├── app/
│   ├── main.py            # App assembly: config/registry/backend/engine/routes/static
│   ├── config.py          # Environment-based config
│   ├── schemas.py         # /tts request model (Mambo compatible)
│   ├── registry.py        # Voice registry (JSON persistence, thread-safe)
│   ├── engine.py          # Thread pool + serial lock + cut_punc split + max_sec truncation
│   ├── backends/
│   │   ├── base.py        # Backend interface
│   │   ├── gsv.py         # Real GPT-SoVITS inference (TTS_infer_pack, hot weight switch)
│   │   └── mock.py        # Demo backend (zero-dependency sine wave)
│   ├── routes_tts.py      # POST /tts
│   ├── routes_admin.py    # /api/* admin endpoints
│   └── static/            # Web console (vanilla HTML/JS/CSS)
├── scripts/
│   ├── smoke_test.py          # Smoke test
│   ├── quantize_pretrained.py # Pre-quantize BERT/HuBERT to int8 (low-memory CPU)
│   └── public_e2e.py          # End-to-end test against a deployed instance
├── data/                  # Runtime generated: voices.json + voices/<name>/
└── requirements.txt
```

## Deployment Notes

- Set `TTS_ADMIN_TOKEN` in production and reverse-proxy with Nginx (raise `client_max_body_size` to fit weight files, ~500 MB per voice)
- GPT-SoVITS officially recommends Python 3.10; this service's own code runs on 3.10+, and the demo backend works even on 3.14
- For long texts, use a client timeout of 120s+; `max_sec` effectively prevents runaway CPU inference
- CPU inference needs real memory: a 2-core / 1.6 GB VPS will swap-thrash and freeze — do not attempt without ≥ 4 GB RAM. GPU inference (CUDA) is strongly recommended and is 10-50x faster
