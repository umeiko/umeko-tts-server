# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

A text-to-speech service and admin console built on [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS). It exposes a `/tts` endpoint fully compatible with the Mambo TTS API, with multi-voice management, weight upload, concurrent queuing, and a built-in web console.

## Features

- **Mambo TTS API compatible**: `POST /tts` with the same parameters, defaults, and error format (`{"message": "..."}`) — drop-in replacement for existing clients
- **Multi-voice management**: create / edit / delete voices, upload GPT weights (.ckpt), SoVITS weights (.pth), and reference audio; Chinese voice names supported
- **Hot weight switching**: the model swaps automatically per requested voice; loaded weights are cached and reused, so switching costs nothing
- **Concurrency**: requests run in a thread pool (auto-sized from CPU cores + available memory); inference is guarded by a global serial lock — requests queue up cleanly, never race, and the event loop never blocks
- **Web console**: voice management, online audition, live queue status, API docs — pure static pages, no build step
- **Prebuilt voice packs**: one-command install from GitHub Release (`scripts/download_voices.py`)

## Requirements

| Backend | Minimum | Recommended | Speed (short sentence) |
|---|---|---|---|
| **CUDA GPU** | NVIDIA card, 6 GB VRAM, 8 GB RAM | RTX 3060 12 GB class, 16 GB RAM | ~2–5 s (measured on RTX 3060 Laptop) |
| **Apple Silicon** | M1, 8 GB unified memory | M2/M3/M4, 16 GB | seconds to tens of seconds (community estimates, not measured) |
| **CPU only** | x86_64, 4 GB RAM (with int8 pre-quantization) | modern multi-core CPU, 16 GB RAM | ~1–2 min (desktop CPU) |

Disk: ~10 GB (torch + pretrained models ~2 GB + ~300 MB per voice).

**Do not deploy CPU inference on small VPS** — a 2-core / 1.6 GB instance will swap-thrash and freeze. Use at least 4 GB RAM, preferably 8 GB.

## Deployment

### 1. Clone (all platforms)

```bash
git clone https://github.com/umeiko/umeko-tts-server.git
cd umeko-tts-server
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS
```

### 2. Python environment (all platforms)

Python **3.10 or 3.11**. [`uv`](https://github.com/astral-sh/uv) recommended:

```bash
uv venv .venv --python 3.11
# Linux / macOS:  PY=.venv/bin/python
# Windows:        PY=.venv/Scripts/python.exe
uv pip install --python $PY -r requirements.txt
```

### 3. Install torch (pick your platform)

**NVIDIA GPU (Windows / Linux, CUDA 12.4):**

```bash
uv pip install --python $PY torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu124
```

**Apple Silicon (M1–M4, macOS 12.3+):** PyPI wheels include MPS support, nothing special needed:

```bash
uv pip install --python $PY torch torchaudio
```

**CPU only:**

```bash
uv pip install --python $PY torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
```

### 4. GPT-SoVITS runtime dependencies

```bash
uv pip install --python $PY transformers librosa soundfile onnxruntime \
  langsegment jieba pypinyin cn2an g2p_en fast_langdetect split-lang \
  wordsegment ToJyutping g2pk2 ko_pron opencc-python-reimplemented \
  pyopenjtalk-prebuilt sentencepiece rotary_embedding_torch x_transformers \
  ffmpeg-python pytorch-lightning torchmetrics matplotlib peft
```

(Authoritative list: `GPT-SoVITS/requirements.txt`. On Linux, also `apt install ffmpeg` for audio tooling.)

English synthesis additionally needs NLTK corpora:

```bash
$PY -c "import nltk; nltk.download('cmudict'); nltk.download('averaged_perceptron_tagger_eng')"
# behind a proxy: prefix with NLTK_ALLOW_PROXIED_URLOPEN=1
```

### 5. Pretrained models

Download into `GPT-SoVITS/GPT_SoVITS/pretrained_models/` (from [huggingface.co/lj1995/GPT-SoVITS](https://huggingface.co/lj1995/GPT-SoVITS), or `hf-mirror.com` in China):

- `chinese-roberta-wwm-ext-large/`
- `chinese-hubert-base/`
- `sv/pretrained_eres2netv2w24s4ep4.ckpt`
- `fast_langdetect/lid.176.bin`

### 6. Voice packs

```bash
$PY scripts/download_voices.py            # all prebuilt voices
$PY scripts/download_voices.py mambo      # or specific ones
# slow GitHub connection? use a mirror: --proxy-prefix https://ghfast.top/
```

Or add your own voices later through the web console.

### 7. Run

```bash
# Linux / macOS
TTS_BACKEND=gsv GSV_DEVICE=auto $PY -m app.main

# Windows PowerShell
$env:TTS_BACKEND="gsv"; $env:GSV_DEVICE="auto"; & .venv/Scripts/python.exe -m app.main
```

`GSV_DEVICE=auto` picks CUDA → MPS → CPU automatically. On CPU-only machines with little RAM, pre-quantize the pretrained models first (`$PY scripts/quantize_pretrained.py`) to avoid the fp32 load-then-quantize memory spike.

Open the console: <http://127.0.0.1:9880/console/>

## Configuration (Environment Variables)

| Variable | Default | Description |
|------|--------|------|
| `TTS_HOST` | `0.0.0.0` | Listen address |
| `TTS_PORT` | `9880` | Listen port |
| `TTS_BACKEND` | `auto` | `auto` (use GPT-SoVITS when available) / `gsv` (require it, fail loudly) |
| `TTS_DATA_DIR` | `./data` | Voice & registry storage directory |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS repo directory |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | Inference config |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `mps` / `cpu` |
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

A usable voice = GPT weights + SoVITS weights + reference audio (+ reference text/language). You can create a name-only voice first (status "incomplete") and upload files later in the console.

## Concurrency Model

```
request → FastAPI event loop → thread pool (loop never blocks)
                                └→ global serial lock → weight switch + inference (queued)
```

- Pool size auto-adapts to hardware; pin it with `TTS_WORKERS`
- The serial lock keeps single-GPU / CPU inference from interleaving; multi-voice requests serialize with automatic weight switching
- The console status bar refreshes queue counts every 3 seconds (pending / running / done / failed)

## Smoke Test

With the service running:

```bash
$PY scripts/smoke_test.py            # default http://127.0.0.1:9880
$PY scripts/smoke_test.py http://host:port
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
│   │   └── gsv.py         # GPT-SoVITS inference (TTS_infer_pack, hot weight switch)
│   ├── routes_tts.py      # POST /tts
│   ├── routes_admin.py    # /api/* admin endpoints
│   └── static/            # Web console (vanilla HTML/JS/CSS)
├── scripts/
│   ├── smoke_test.py          # Smoke test
│   ├── quantize_pretrained.py # Pre-quantize BERT/HuBERT to int8 (low-memory CPU)
│   ├── public_e2e.py          # End-to-end test against a deployed instance
│   ├── pack_voices.py         # Pack voices in data/ into release zips
│   └── download_voices.py     # Download prebuilt voices from GitHub Release
├── data/                  # Runtime generated: voices.json + voices/<name>/
└── requirements.txt
```

## Production Notes

- Set `TTS_ADMIN_TOKEN` and reverse-proxy with Nginx (raise `client_max_body_size` to ~500 MB to fit weight uploads)
- For long texts, use a client timeout of 120 s or more; `max_sec` effectively prevents runaway CPU inference
- On Windows, this project monkey-patches `torchaudio.load` with a soundfile implementation (`app/backends/gsv.py`), so no FFmpeg DLLs are needed for torchaudio ≥ 2.9
- To publish your own voice packs: `$PY scripts/pack_voices.py`
