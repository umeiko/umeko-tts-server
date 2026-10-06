# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的文本转语音服务与管理控制台。对外提供与 Mambo TTS API 完全兼容的 `/tts` 接口，内置多音色管理、权重上传、并发排队与 Web 控制台。

## 功能特性

- **兼容 Mambo TTS API**：`POST /tts`，参数、默认值、错误格式（`{"message": "..."}`）一致，可直接替换现有客户端
- **多音色管理**：新增 / 编辑 / 删除音色，上传 GPT 权重（.ckpt）、SoVITS 权重（.pth）、参考音频；支持中文音色名
- **权重热切换**：按请求的音色自动切换模型，已加载权重缓存复用，切换零开销
- **并发支持**：请求进入线程池（按 CPU 核数 + 可用内存自动适配），推理由全局串行锁保护，多请求自动排队、不竞态、事件循环不阻塞
- **Web 控制台**：音色管理、在线试听、实时队列状态、接口文档，纯静态页面无需构建
- **预置音色包**：从 GitHub Release 一键安装（`scripts/download_voices.py`）

## 硬件需求

| 方案 | 最低配置 | 推荐配置 | 速度参考（短句） |
|---|---|---|---|
| **CUDA 显卡** | N 卡 + 6G 显存 + 8G 内存 | RTX 3060 12G 级 + 16G 内存 | 约 2~5 秒（RTX 3060 Laptop 实测） |
| **苹果 M 芯片** | M1 + 8G 统一内存 | M2/M3/M4 + 16G | 几秒~几十秒（社区经验值，未实测） |
| **纯 CPU** | x86_64 + 4G 内存（需 int8 预量化） | 现代多核 CPU + 16G 内存 | 约 1~2 分钟（桌面 CPU） |

磁盘：约 10 GB（torch 依赖 + 预训练模型约 2 GB + 每个音色约 300 MB）。

**不要在低配 VPS 上跑 CPU 推理**——2 核 / 1.6G 的实例会 swap 颠簸直至假死，内存至少 4G、建议 8G 以上。

## 部署

### 1. 克隆代码（全平台通用）

```bash
git clone https://github.com/umeiko/umeko-tts-server.git
cd umeko-tts-server
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS
```

### 2. Python 环境（全平台通用）

Python **3.10 或 3.11**，推荐用 [`uv`](https://github.com/astral-sh/uv)：

```bash
uv venv .venv --python 3.11
# Linux / macOS:  PY=.venv/bin/python
# Windows:        PY=.venv/Scripts/python.exe
uv pip install --python $PY -r requirements.txt
```

### 3. 安装 torch（按平台选一）

**N 卡（Windows / Linux，CUDA 12.4）：**

```bash
uv pip install --python $PY torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu124
```

**苹果 M 芯片（M1~M4，macOS 12.3+）：** PyPI 官方包自带 MPS 支持，无需特殊源：

```bash
uv pip install --python $PY torch torchaudio
```

**纯 CPU：**

```bash
uv pip install --python $PY torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
```

### 4. GPT-SoVITS 运行时依赖

```bash
uv pip install --python $PY transformers librosa soundfile onnxruntime \
  langsegment jieba pypinyin cn2an g2p_en fast_langdetect split-lang \
  wordsegment ToJyutping g2pk2 ko_pron opencc-python-reimplemented \
  pyopenjtalk-prebuilt sentencepiece rotary_embedding_torch x_transformers \
  ffmpeg-python pytorch-lightning torchmetrics matplotlib peft
```

（权威清单以 `GPT-SoVITS/requirements.txt` 为准；Linux 另需 `apt install ffmpeg`。）

英文合成还需 NLTK 语料：

```bash
$PY -c "import nltk; nltk.download('cmudict'); nltk.download('averaged_perceptron_tagger_eng')"
# 走代理的机器：命令前加 NLTK_ALLOW_PROXIED_URLOPEN=1
```

### 5. 预训练模型

下载到 `GPT-SoVITS/GPT_SoVITS/pretrained_models/`（来源 [huggingface.co/lj1995/GPT-SoVITS](https://huggingface.co/lj1995/GPT-SoVITS)，国内用 `hf-mirror.com`）：

- `chinese-roberta-wwm-ext-large/`
- `chinese-hubert-base/`
- `sv/pretrained_eres2netv2w24s4ep4.ckpt`
- `fast_langdetect/lid.176.bin`

### 6. 音色包

```bash
$PY scripts/download_voices.py            # 安装全部预置音色
$PY scripts/download_voices.py mambo      # 或只装指定的
# GitHub 直连慢：--proxy-prefix https://ghfast.top/
```

之后也可以在 Web 控制台里上传自己的音色。

### 7. 启动

```bash
# Linux / macOS
TTS_BACKEND=gsv GSV_DEVICE=auto $PY -m app.main

# Windows PowerShell
$env:TTS_BACKEND="gsv"; $env:GSV_DEVICE="auto"; & .venv/Scripts/python.exe -m app.main
```

`GSV_DEVICE=auto` 按 CUDA → MPS → CPU 自动选择。低内存纯 CPU 机器建议先跑一次 `$PY scripts/quantize_pretrained.py`（int8 预量化），避免"先读 fp32 再量化"的内存峰值。

打开控制台：<http://127.0.0.1:9880/console/>

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TTS_HOST` | `0.0.0.0` | 监听地址 |
| `TTS_PORT` | `9880` | 监听端口 |
| `TTS_BACKEND` | `auto` | `auto`（有 GPT-SoVITS 就用）/ `gsv`（强制，缺失即报错） |
| `TTS_DATA_DIR` | `./data` | 音色与注册表存储目录 |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS 仓库目录 |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | 推理配置 |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `mps` / `cpu` |
| `GSV_IS_HALF` | `auto` | `auto`（CUDA 下半精度）/ `true` / `false` |
| `TTS_WORKERS` | `0`（自动） | 线程池大小，0 = CPU 核数+4 并按可用内存收缩（上限 32） |
| `TTS_MAX_QUEUE` | `0`（不限） | 最大排队请求数，超出返回 503 |
| `TTS_ADMIN_TOKEN` | 空 | 设置后 `/api/*` 需请求头 `X-Admin-Token` |
| `TTS_CORS_ORIGINS` | `*` | 允许的跨域来源，逗号分隔 |

## TTS 合成接口

`POST /tts` · `Content-Type: application/json`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| text | string | 是 | — | 要合成的文本，支持中/英/日/韩/粤语 |
| voice | string | 否 | 默认音色 | 扩展参数：音色名称 |
| text_lang | string | 否 | zh | zh / en / ja / ko / yue / auto |
| speed_factor | float | 否 | 1.0 | 语速倍率，0.5~2.0（越界自动钳位） |
| top_k | int | 否 | 15 | GPT 采样 top-k，1~100 |
| top_p | float | 否 | 1.0 | 核采样阈值，0~1 |
| temperature | float | 否 | 1.0 | 采样温度，0.1~2.0 |
| cut_punc | string | 否 | "" | 手动指定切分标点（如 `。！？`），留空自动切分 |
| max_sec | float | 否 | 10.0 | 最大音频秒数，上限 60，超出截断 |

- 成功 (200)：`audio/wav` 二进制，响应头附带 `X-Voice`（URL 编码的音色名）与 `X-Inference-Ms`
- 失败 (4xx/5xx)：`{"message": "错误描述"}`

```bash
# 最小调用
curl -X POST http://127.0.0.1:9880/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "你好世界"}' -o output.wav
```

## 管理接口（/api/*）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/status | 服务状态：后端、设备、线程数、队列计数、运行时长 |
| GET | /api/voices | 音色列表 |
| POST | /api/voices | 新增音色（multipart：`name`*、`description`、`ref_text`、`ref_lang`、`gpt_file`、`sovits_file`、`ref_audio`） |
| PUT | /api/voices/{name} | 更新音色（字段同上，均可选；同名文件自动替换） |
| DELETE | /api/voices/{name} | 删除音色及其全部文件 |
| POST | /api/voices/{name}/default | 设为默认音色 |
| GET | /api/voices/{name}/ref-audio | 下载 / 试听参考音频 |

一个可用音色 = GPT 权重 + SoVITS 权重 + 参考音频（+ 参考文本/语种）。可以先只填名称创建（状态「待完善」），后续在控制台补齐文件。

## 并发模型

```
请求 → FastAPI 事件循环 → 线程池执行（不阻塞 loop）
                           └→ 全局串行锁 → 权重切换 + 推理（逐请求排队）
```

- 线程池大小自动适配硬件，也可用 `TTS_WORKERS` 固定
- 串行锁保证单卡 / CPU 推理不会并发交错，多音色请求自动串行切换权重
- 控制台状态栏每 3 秒刷新队列计数（待处理 / 执行中 / 完成 / 失败）

## 冒烟测试

服务运行中执行：

```bash
$PY scripts/smoke_test.py            # 默认 http://127.0.0.1:9880
$PY scripts/smoke_test.py http://host:port
```

覆盖：状态、音色 CRUD（含中文名与文件上传）、最小 / 完整 / 非法参数、错误格式、8 路并发、max_sec 截断、控制台页面。

## 目录结构

```
umeko-tts-server/
├── app/
│   ├── main.py            # 应用装配：配置/注册表/后端/引擎/路由/静态页
│   ├── config.py          # 环境变量配置
│   ├── schemas.py         # /tts 请求模型（Mambo 兼容）
│   ├── registry.py        # 音色注册表（JSON 持久化，线程安全）
│   ├── engine.py          # 线程池 + 串行锁 + cut_punc 切分 + max_sec 截断
│   ├── backends/
│   │   ├── base.py        # 后端接口
│   │   └── gsv.py         # GPT-SoVITS 推理（TTS_infer_pack，权重热切换）
│   ├── routes_tts.py      # POST /tts
│   ├── routes_admin.py    # /api/* 管理接口
│   └── static/            # Web 控制台（原生 HTML/JS/CSS）
├── scripts/
│   ├── smoke_test.py          # 冒烟测试
│   ├── quantize_pretrained.py # 预训练模型 int8 离线量化（低内存 CPU 用）
│   ├── public_e2e.py          # 针对已部署实例的端到端测试
│   ├── pack_voices.py         # 把 data/ 里的音色打包成 release zip
│   └── download_voices.py     # 从 GitHub Release 下载预置音色
├── data/                  # 运行时生成：voices.json + voices/<音色>/
└── requirements.txt
```

## 生产部署提示

- 建议设置 `TTS_ADMIN_TOKEN`，并用 Nginx 反代（`client_max_body_size` 调到 500MB 左右以容纳权重上传）
- 长文本建议客户端设置 120 秒以上超时；`max_sec` 可有效防止 CPU 推理失控
- Windows 上本项目把 `torchaudio.load` 补丁为 soundfile 实现（`app/backends/gsv.py`），torchaudio ≥ 2.9 无需安装 FFmpeg DLL
- 发布自己的音色包：`$PY scripts/pack_voices.py`
