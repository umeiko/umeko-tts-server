# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的文本转语音服务与管理控制台。对外提供与 Mambo TTS API 完全兼容的 `/tts` 接口，内置多音色管理、权重上传、并发排队与 Web 控制台。

## 功能特性

- **兼容 Mambo TTS API**：`POST /tts`，参数、默认值、错误格式（`{"message": "..."}`）一致，可直接替换现有客户端
- **多音色管理**：新增 / 编辑 / 删除音色，上传 GPT 权重（.ckpt）、SoVITS 权重（.pth）、参考音频；支持中文音色名
- **权重热切换**：按请求的音色自动切换模型，已加载权重缓存复用，切换零开销
- **并发支持**：请求进入线程池（按 CPU 核数 + 可用内存自动适配），推理由全局串行锁保护，多请求自动排队、不竞态、事件循环不阻塞
- **Web 控制台**：音色管理、在线试听、实时队列状态、接口文档，纯静态页面无需构建
- **演示后端**：未安装 GPT-SoVITS 时自动回退到正弦波演示后端，API / 控制台 / 并发行为可完整自检

## 快速开始（演示模式）

```bash
pip install -r requirements.txt
python -m app.main
```

打开控制台：<http://127.0.0.1:9880/console/>

此时为演示后端（mock），输出正弦波测试音频，用于验证服务与管理功能。

## 接入 GPT-SoVITS（真实合成）

```bash
# 1. 克隆仓库（放到项目根目录，或用 GSV_ROOT 指向其他位置）
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS

# 2. 建虚拟环境（推荐 Python 3.10/3.11，用 uv 最省事）
uv venv .venv --python 3.11

# 3. 安装 torch —— 二选一：
#    N 卡（CUDA 12.4，如 RTX 30/40 系）：
uv pip install --python .venv/Scripts/python.exe torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu124
#    纯 CPU：
uv pip install --python .venv/Scripts/python.exe torch torchaudio

# 4. 安装 GPT-SoVITS 依赖及运行时附加包：
#    transformers librosa soundfile onnxruntime langsegment jieba pypinyin
#    cn2an g2p_en fast_langdetect split-lang wordsegment ToJyutping g2pk2
#    ko_pron opencc-python-reimplemented pyopenjtalk-prebuilt sentencepiece
#    rotary_embedding_torch x_transformers ffmpeg-python pytorch-lightning
#    torchmetrics matplotlib peft
#    （权威清单以 GPT-SoVITS/requirements.txt 为准）

# 5. 下载预训练模型到 GPT-SoVITS/GPT_SoVITS/pretrained_models/：
#    - chinese-roberta-wwm-ext-large/
#    - chinese-hubert-base/
#    - sv/pretrained_eres2netv2w24s4ep4.ckpt
#    - fast_langdetect/lid.176.bin
#    （均可从 huggingface.co/lj1995/GPT-SoVITS 或 hf-mirror.com 获取）

# 6. 启动（强制真实后端，失败即报错）
TTS_BACKEND=gsv python -m app.main   # Linux / macOS
# Windows cmd:   set TTS_BACKEND=gsv && python -m app.main
# PowerShell:    $env:TTS_BACKEND="gsv"; python -m app.main
```

说明：

- 服务启动时会将工作目录切换到 `GSV_ROOT`，以兼容官方 `tts_infer.yaml` 中预训练模型的相对路径；本服务的所有数据路径均为绝对路径，不受影响。
- **Windows**：torchaudio ≥ 2.9 的 `load()` 默认走 torchcodec，需要完整版 FFmpeg DLL。本项目在 `app/backends/gsv.py` 中把 `torchaudio.load` 补丁为 soundfile 实现，无需安装 FFmpeg。
- **低内存 CPU 机器**：`scripts/quantize_pretrained.py` 可将 BERT/HuBERT 预训练模型离线量化为 int8 旁挂文件；CPU 模式下后端直接加载量化文件，避免"先读 fp32 再量化"的内存峰值。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TTS_HOST` | `0.0.0.0` | 监听地址 |
| `TTS_PORT` | `9880` | 监听端口 |
| `TTS_BACKEND` | `auto` | `auto`（有 GSV 用 GSV，否则 mock）/ `gsv`（强制真实后端）/ `mock`（演示） |
| `TTS_DATA_DIR` | `./data` | 音色与注册表存储目录 |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS 仓库目录 |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | 推理配置 |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
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

一个可用音色 = GPT 权重 + SoVITS 权重 + 参考音频（+ 参考文本/语种）。可以先只填名称创建（状态「待完善」），后续在控制台补齐文件；真实后端要求三项文件齐全。

## 并发模型

```
请求 → FastAPI 事件循环 → 线程池执行（不阻塞 loop）
                           └→ 全局串行锁 → 权重切换 + 推理（逐请求排队）
```

- 线程池大小自动适配硬件，也可用 `TTS_WORKERS` 固定
- 串行锁保证单卡 / CPU 推理不会并发交错，多音色请求自动串行切换权重
- 控制台状态栏每 3 秒刷新队列计数（待处理 / 执行中 / 完成 / 失败）

## 冒烟测试

服务运行中执行（演示后端即可全量通过）：

```bash
python scripts/smoke_test.py            # 默认 http://127.0.0.1:9880
python scripts/smoke_test.py http://host:port
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
│   │   ├── gsv.py         # GPT-SoVITS 真实推理（TTS_infer_pack，权重热切换）
│   │   └── mock.py        # 演示后端（零依赖正弦波）
│   ├── routes_tts.py      # POST /tts
│   ├── routes_admin.py    # /api/* 管理接口
│   └── static/            # Web 控制台（原生 HTML/JS/CSS）
├── scripts/
│   ├── smoke_test.py          # 冒烟测试
│   ├── quantize_pretrained.py # 预训练模型 int8 离线量化（低内存 CPU 用）
│   └── public_e2e.py          # 针对已部署实例的端到端测试
├── data/                  # 运行时生成：voices.json + voices/<音色>/
└── requirements.txt
```

## 部署提示

- 生产环境建议设置 `TTS_ADMIN_TOKEN`，并用 Nginx 反代（注意调大 `client_max_body_size` 以容纳权重文件，单音色约 500MB）
- GPT-SoVITS 官方推荐 Python 3.10；本服务自身代码兼容 3.10+，演示后端在更高版本（含 3.14）可直接运行
- 长文本建议客户端设置 120 秒以上超时；`max_sec` 可有效防止 CPU 推理失控
- CPU 推理对内存要求不低：2 核 / 1.6G 的 VPS 会 swap 颠簸直至假死，**未升配到 ≥4G 内存前请勿尝试**；强烈建议使用 CUDA GPU 推理，速度比 CPU 快 10~50 倍
