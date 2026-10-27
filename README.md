# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

A text-to-speech service and admin console built on [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS). It exposes a `/tts` endpoint fully compatible with the Mambo TTS API, with multi-voice management, weight upload, concurrent queuing, and a built-in web console.

**v2 security upgrade:** synthesis now requires an administrator-issued API Key. Anonymous management is disabled. The `/tts` body and WAV response are preserved; add `Authorization: Bearer <API_KEY>` to existing clients. Set `TTS_ADMIN_TOKEN`, sign in, create a trusted user, then issue a key. See [deployment and ALB prefix configuration](docs/deployment.md).

## Features

- **Mambo TTS API compatible**: `POST /tts` with the same parameters, defaults, and error format (`{"message": "..."}`) — drop-in replacement for existing clients
- **Multi-voice management**: create / edit / delete voices, upload GPT weights (.ckpt), SoVITS weights (.pth), and reference audio; Chinese voice names supported
- **Hot weight switching**: the model swaps automatically per requested voice; already loaded weights are reused; changing voices can reload weights
- **Trusted-user access**: hashed API keys, user policies, expiry, rotation and revocation; browser admin sessions with CSRF protection
- **Bounded scheduling**: global/per-user concurrency and queues, request/character quotas, priority with waiting-time aging
- **Long jobs**: submit, poll, download and cancel asynchronous jobs behind an ALB
- **MCP**: authenticated Streamable HTTP at `/mcp`; voice discovery, queued synthesis, progress, WAV audio and cancellation share the same user policies
- **Web console**: voice management, online audition, task stages/segment progress, expandable admin-only error tracebacks, and API docs with voice selection — pure static pages, no build step
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
$PY scripts/download_voices.py nina       # Nina Iseri character voice
# slow GitHub connection? use a mirror: --proxy-prefix https://ghfast.top/
```

Or add your own voices later through the web console.

### Nina Iseri character voice

The **仁菜** (Nina Iseri) character voice is separately available as `nina.zip`. Install it with `scripts/download_voices.py nina`, then select **仁菜** in the console or pass `"voice":"仁菜"` in REST/MCP requests. It uses GPT-SoVITS v2 fine-tuned weights (character revision V3) and a separately sourced public Japanese character reference; provenance, hashes and non-commercial usage notes are included. Server code licensing does not grant character voice rights.
For local Windows CPU deployment use `scripts/start_local_cpu.ps1 -InstallNina` after installing the runtime and pretrained models. See [deployment and MCP](docs/deployment.md).

### Mandarin narrator presets

| Asset | API voice name | Reference voice |
|---|---|---|
| `narrator_male.zip` | `播音男声` | Kokoro 1.1 Chinese `zm_010` |
| `narrator_female.zip` | `播音女声` | Kokoro 1.1 Chinese `zf_001` |

```bash
$PY scripts/download_voices.py narrator_male narrator_female
# Or install downloaded assets without accessing GitHub:
$PY scripts/download_voices.py --file narrator_male.zip --file narrator_female.zip
```

These are **zero-shot narration presets, not separately fine-tuned announcer checkpoints**.
Both contain the same official GPT-SoVITS v2ProPlus base weights (MIT); different
synthetic references from [Kokoro-82M-v1.1-zh](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)
(Apache-2.0) condition their timbre and delivery. Its model card describes Chinese
training data permissively granted by LongMaoData. No news-program or celebrity
recordings are included.

Each pack includes reference text/audio, pinned revisions and SHA256 hashes in
`PROVENANCE.json`, licenses/model cards, and an API-generated `preview.wav`.
The installer verifies supplied hashes and preserves attribution. Existing
default-voice selection is preserved. Restart a running server after installing.
Serving these packs uses the existing GPT-SoVITS backend and needs no Kokoro dependency.

Maintainers can rebuild the references with `scripts/prepare_narrators.py`
(optional development dependencies: `kokoro==0.9.4`, `misaki[zh]==0.9.4`), then run
`scripts/pack_voices.py 播音男声 播音女声`. Reference creation refuses to overwrite
existing voice directories; use `--data-dir` for an isolated build directory.

### 7. Run

Generate and privately save a random admin token, then set `TTS_ADMIN_TOKEN` before starting (at least 24 characters):

```bash
$PY -c "import secrets; print(secrets.token_urlsafe(32))"
# Linux/macOS: export TTS_ADMIN_TOKEN='<generated token>'
# PowerShell: $env:TTS_ADMIN_TOKEN='<generated token>'
```

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
| `TTS_BACKEND` | `gsv` | `gsv` / `auto` require a working real backend; explicit `mock` is for testing |
| `TTS_DATA_DIR` | `./data` | Voice & registry storage directory |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS repo directory |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | Inference config |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `mps` / `cpu` |
| `GSV_IS_HALF` | `auto` | `auto` (half precision on CUDA) / `true` / `false` |
| `TTS_WORKERS` | `0` (auto) | Thread budget, capped by backend capacity; GPT-SoVITS supports 1 shared-pipeline inference |
| `TTS_MAX_QUEUE` | `64` | Initial waiting queue limit, excluding running work; overflow returns 429 |
| `TTS_ADMIN_TOKEN` | empty | Required admin credential, at least 24 characters; unset closes management |
| `TTS_CORS_ORIGINS` | empty | Same origin by default; explicitly list API client origins if needed |
| `UMEKO_TTS_BASE_URL` | empty | Public URL or path prefix, e.g. `https://tts.example.com/services/voice` |
| `TTS_CONCURRENCY` | `1` | Initial global inference concurrency, bounded by backend capacity |
| `TTS_QUEUE_TIMEOUT` | `120` | Initial queue wait timeout in seconds |
| `TTS_PRIORITY_AGING_SECONDS` | `15` | Priority increases by one after this waiting interval |
| `TTS_MAX_TEXT_CHARS` | `4000` | Service-wide input character cap; user cap also applies |
| `TTS_RESULT_TTL` / `TTS_RESULT_MAX_MB` | `600` / `64` | In-memory result retention seconds / total MiB |
| `TTS_SESSION_SECONDS` / `TTS_MAX_UPLOAD_MB` | `28800` / `512` | Admin session lifetime / multipart request cap |
| `TTS_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs/CIDRs for the built-in launcher |

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
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}' -o output.wav
```

## Admin API (/api/*)

Browser sessions require CSRF on writes; admin scripts may send `X-Admin-Token`. API Keys cannot administer the service. User/key issuance, revocation/rotation, queue cancellation, scheduler settings and audit endpoints are described in [deployment](docs/deployment.md); authenticated schema: `/api/openapi`.

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

Global and per-user slots govern a bounded priority queue. Higher user priority (0–10) runs first; equal priority is FIFO. Waiting requests gain priority over time to prevent starvation. GPT-SoVITS has one shared pipeline, so its safe inference capacity is 1. Cancelled running jobs keep their slot until real inference exits.

Keys belonging to one user share limits. Accepted characters are reserved; success commits usage, while failure/timeout/cancellation releases it. Accepted requests still count toward RPM. Daily quotas use UTC. Console changes to concurrency/queue policy persist across restarts.

Use `POST /v1/jobs`, poll `GET /v1/jobs/{id}`, then download `/audio`; include an API Key on every request. Jobs/results are memory-local and expire. Run one instance/ASGI worker; this version does not provide cross-instance global scheduling. See [deployment boundaries](docs/deployment.md).

## Smoke Test

Isolated regression tests need no model weights and do not touch a running service:

```bash
uv pip install --python $PY -r requirements-dev.txt
$PY -m pytest tests -q
```

Covers authentication/CSRF, key lifecycle, shared quotas, priority/aging, cancellation, user isolation and root/nested proxy prefixes. `scripts/smoke_test.py <base_url>` checks a deployed instance with a dedicated `UMEKO_TTS_API_KEY` without changing voices.

## Directory Layout

```
umeko-tts-server/
├── app/
│   ├── main.py            # App assembly: config/registry/backend/engine/routes/static
│   ├── config.py          # Environment-based config
│   ├── schemas.py         # /tts request model (Mambo compatible)
│   ├── registry.py        # Voice registry (JSON persistence, thread-safe)
│   ├── engine.py          # Bounded priority queue, user policies and safe inference slots
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

- Configure a random `TTS_ADMIN_TOKEN`, HTTPS `UMEKO_TTS_BASE_URL`, trusted proxy IPs and private origin access; see [deployment](docs/deployment.md).
- Use asynchronous jobs for long requests behind an ALB. `max_sec` caps output duration, not the runtime of an individual model call.
- Set `voice` to the exact `name` returned by authenticated `GET /v1/voices` in either `/tts` or `/v1/jobs`; omit it to use the default voice. The console's API docs generate both examples for your selected voice. See [voice selection, progress and diagnostics](docs/deployment.md#指定音色).
- Job status exposes actual stages/segment counts; single-segment inference has no internal percentage. Admin-only request details retain captured Python exception chains for 30 days, including across restarts; older records cannot recover traces that were never saved.
- Expanded administrator records include the actual synthesis text/parameters, queue time, inference time (including weight loading/WAV encoding) and returned audio duration. They persist for 30 days and are excluded from ordinary job status/MCP tools and list polling. Older inputs cannot be recovered retroactively.
- On Windows, this project monkey-patches `torchaudio.load` with a soundfile implementation (`app/backends/gsv.py`), so no FFmpeg DLLs are needed for torchaudio ≥ 2.9
- To publish your own voice packs: `$PY scripts/pack_voices.py`
