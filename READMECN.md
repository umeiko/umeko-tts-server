# Umeko TTS Server

[English](README.md) | [简体中文](READMECN.md)

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的文本转语音服务与管理控制台。对外提供与 Mambo TTS API 完全兼容的 `/tts` 接口，内置多音色管理、权重上传、并发排队与 Web 控制台。

**v2 升级说明：合成需要管理员发放的 API Key，管理接口不再允许匿名访问。** `/tts` 请求体与 WAV 响应保留，老客户端需增加 `Authorization: Bearer <API_KEY>`。先设置 `TTS_ADMIN_TOKEN`、登录面板、创建可信用户，再发放密钥。前缀与 ALB 部署见 [部署文档](docs/deployment.md)。

## 功能特性

- **兼容 Mambo TTS API**：`POST /tts`，参数、默认值、错误格式（`{"message": "..."}`）一致，可直接替换现有客户端
- **多音色管理**：新增 / 编辑 / 删除音色，上传 GPT 权重（.ckpt）、SoVITS 权重（.pth）、参考音频；支持中文音色名
- **权重热切换**：按请求的音色自动切换模型，已加载权重可复用，不同音色切换可能重新加载模型
- **访问管理**：用户启停、密钥摘要存储、有效期、轮换与撤销；管理员会话与 CSRF 保护
- **有界调度**：全局/用户并发与排队上限、请求/字符额度、优先级与等待时间提升
- **长任务接口**：异步提交、查询、下载与取消，适合多人排队与 ALB 访问
- **Web 控制台**：音色管理、在线试听、实时队列状态、接口文档，纯静态页面无需构建
- **预置音色包**：从 GitHub Release 一键安装（`scripts/download_voices.py`）
- **MCP 接入**：`/mcp` 提供 Streamable HTTP 入口，复用 API Key、用户额度与队列，支持查询音色、合成、进度、音频和取消

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
$PY scripts/download_voices.py nina       # 仁菜（井芹仁菜）
# GitHub 直连慢：--proxy-prefix https://ghfast.top/
```

之后也可以在 Web 控制台里上传自己的音色。

### 仁菜（井芹仁菜）音色

安装 `nina.zip` 后，在“音色与试听”选择 **仁菜**，或在 HTTP / MCP 合成中传入 `"voice":"仁菜"`。
权重使用 GPT-SoVITS v2 架构，文件名 V3 表示音色修订号；另配同角色公开日语合成样本作为参考音频，包内保留来源、校验和非商业使用说明。服务器代码许可证不授予角色音色商业权利。
Windows 本机 CPU 启动：安装推理环境和预训练模型后运行 `scripts/start_local_cpu.ps1 -InstallNina`。
网页、API 与 MCP 共用服务，首次生成的管理员令牌保存在本机 `data/admin-token.txt`。详见[部署与 MCP 接入](docs/deployment.md)。

### 正式中文旁白音色

| 资源文件 | API 音色名称 | 参考音色 |
|---|---|---|
| `narrator_male.zip` | `播音男声` | Kokoro 1.1 中文 `zm_010` |
| `narrator_female.zip` | `播音女声` | Kokoro 1.1 中文 `zf_001` |

```bash
$PY scripts/download_voices.py narrator_male narrator_female
# 也可以安装已下载的文件，无需访问 GitHub：
$PY scripts/download_voices.py --file narrator_male.zip --file narrator_female.zip
```

两者是**零样本旁白音色预设，不是分别微调的播音员专属检查点**。
包内共享同一对官方 GPT-SoVITS v2ProPlus 基座权重（MIT）；音色与语气由
[Kokoro-82M-v1.1-zh](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)
（Apache-2.0）合成的不同参考音频决定。其模型卡说明，中文训练语料由龙猫数据授权提供。
包内没有截取新闻节目或名人录音。

每包包含参考文本与音频、记录固定版本和 SHA256 的 `PROVENANCE.json`、许可与模型卡，
以及通过本项目 API 生成的 `preview.wav`。安装时会验证包内提供的哈希并保留来源文件，
保留现有默认音色；安装后需重启已运行的服务。推理仍使用现有 GPT-SoVITS 后端，无需安装 Kokoro。

维护者可用 `scripts/prepare_narrators.py` 重新生成参考音频
（仅制作时需要 `kokoro==0.9.4`、`misaki[zh]==0.9.4`），再运行
`scripts/pack_voices.py 播音男声 播音女声` 打包。制作脚本拒绝覆盖已有音色目录，
可用 `--data-dir` 指定独立制作目录。

### 7. 启动

启动前生成并妥善保存随机管理员令牌，设置为 `TTS_ADMIN_TOKEN`（至少 24 字符）：

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

`GSV_DEVICE=auto` 按 CUDA → MPS → CPU 自动选择。低内存纯 CPU 机器建议先跑一次 `$PY scripts/quantize_pretrained.py`（int8 预量化），避免"先读 fp32 再量化"的内存峰值。

打开控制台：<http://127.0.0.1:9880/console/>

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TTS_HOST` | `0.0.0.0` | 监听地址 |
| `TTS_PORT` | `9880` | 监听端口 |
| `TTS_BACKEND` | `gsv` | 真实后端失败即停止；`mock` 仅用于明确选择的本地测试 |
| `TTS_DATA_DIR` | `./data` | 音色与注册表存储目录 |
| `GSV_ROOT` | `./GPT-SoVITS` | GPT-SoVITS 仓库目录 |
| `GSV_CONFIG` | `<GSV_ROOT>/GPT_SoVITS/configs/tts_infer.yaml` | 推理配置 |
| `GSV_DEVICE` | `auto` | `auto` / `cuda` / `mps` / `cpu` |
| `GSV_IS_HALF` | `auto` | `auto`（CUDA 下半精度）/ `true` / `false` |
| `TTS_WORKERS` | `0`（自动） | 推理线程预算，受后端容量限制；GPT-SoVITS 共享管线上限 1 |
| `TTS_MAX_QUEUE` | `64` | 首次启动时的等待队列上限，不含运行中任务；超出返回 429 |
| `TTS_ADMIN_TOKEN` | 空 | 管理员登录凭证，至少 24 字符；未配置则关闭管理接口 |
| `TTS_CORS_ORIGINS` | 空 | 默认同源；可显式列出 API 客户端来源 |
| `UMEKO_TTS_BASE_URL` | 空 | 外部完整 URL 或前缀，如 `https://tts.example.com/services/voice` |
| `TTS_CONCURRENCY` | `1` | 首次启动时的全局推理并行数，受后端容量限制 |
| `TTS_QUEUE_TIMEOUT` | `120` | 首次启动时的排队超时秒数 |
| `TTS_PRIORITY_AGING_SECONDS` | `15` | 每等待此秒数，调度优先级增加 1 |
| `TTS_MAX_TEXT_CHARS` | `4000` | 服务单次字符硬上限，同时受用户上限约束 |
| `TTS_RESULT_TTL` / `TTS_RESULT_MAX_MB` | `600` / `64` | 内存结果保留秒数 / 总缓存 MiB |
| `TTS_SESSION_SECONDS` / `TTS_MAX_UPLOAD_MB` | `28800` / `512` | 管理员会话秒数 / 上传请求体大小上限 |
| `TTS_FORWARDED_ALLOW_IPS` | `127.0.0.1` | 内置启动入口所信任的代理 IP/CIDR |

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

指定音色：先携带 API Key 请求 `GET /v1/voices`，将返回数组中完整的 `name` 填入请求 JSON 的 `voice`，中文名称可直接使用。
`POST /tts` 与 `POST /v1/jobs` 都支持此字段，省略或传 `null` 时使用默认音色。
例如 `{"voice":"播音女声","text":"你好"}`，其中名称需替换为实际查询结果；不存在返回 404，文件未齐返回 400。
网页“接入文档”可选择当前可用音色，自动生成同步/异步 curl 示例；完整示例见[指定音色](docs/deployment.md#指定音色)。

任务状态接口新增真实阶段、已用时间与分段完成数；单段模型推理没有内部百分比，网页显示进行中动画。
管理员可展开调用记录查看并复制原始 Python 异常链，诊断随记录保留 30 天，重启后仍可查看；普通 API Key 无权读取。
展开详情还可查看实际合成输入与参数、排队时长、合成耗时和返回音频时长；这些内容仅管理员按需读取，列表、普通任务状态与 MCP 工具结果不返回输入正文。
旧记录无法补回未采集的错误。细节见[进度与错误详情](docs/deployment.md#进度与管理员错误详情)。

```bash
# 最小调用
curl -X POST http://127.0.0.1:9880/tts \
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "你好世界"}' -o output.wav
```

## 管理接口（/api/*）

浏览器使用会话 Cookie 与写操作 CSRF；管理员自动化可发送 `X-Admin-Token`。普通 API Key 无管理权限。用户/密钥的创建与启停、撤销/轮换、队列取消、调度设置与审计接口见 [部署文档](docs/deployment.md)，登录后可获取 `/api/openapi`。

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

全局与每用户名额共同限制有界队列。用户优先级 0–10，大者优先，同级 FIFO；等待中的任务逐步提升优先级，避免饥饿。GPT-SoVITS 共享一份模型管线，安全推理容量为 1。取消运行中的任务后，实际推理结束才释放名额。

同一用户的多把密钥共享限额。接受请求时预留字符，成功后结算，失败/超时/取消释放；已接受请求仍计入每分钟限额。每日额度按 UTC 日界线统计。面板修改的并发与队列设置会持久保存。

长任务使用 `POST /v1/jobs` 提交、`GET /v1/jobs/{id}` 查询、`/audio` 下载，每次携带 API Key。任务和音频位于内存且会过期。当前运行一个实例与一个 ASGI worker，不提供跨实例全局队列，详见 [部署边界](docs/deployment.md)。

## 冒烟测试

隔离回归无需模型权重，也不会访问或删除运行中服务的音色：

```bash
uv pip install --python $PY -r requirements-dev.txt
$PY -m pytest tests -q
```

覆盖鉴权/CSRF、密钥生命周期、共享额度、优先级与老化、取消、用户隔离和根路径/多级代理前缀。`scripts/smoke_test.py <base_url>` 可通过专用 `UMEKO_TTS_API_KEY` 检查已部署实例，不修改音色。

## 目录结构

```
umeko-tts-server/
├── app/
│   ├── main.py            # 应用装配：配置/注册表/后端/引擎/路由/静态页
│   ├── config.py          # 环境变量配置
│   ├── schemas.py         # /tts 请求模型（Mambo 兼容）
│   ├── registry.py        # 音色注册表（JSON 持久化，线程安全）
│   ├── engine.py          # 有界优先级队列、用户策略与安全推理名额
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

- 配置随机管理员令牌、HTTPS `UMEKO_TTS_BASE_URL`、可信代理与源站访问限制，详见 [部署文档](docs/deployment.md)。
- 通过 ALB 访问长任务时使用异步接口；`max_sec` 限制输出长度，不能保证中断单次模型调用。
- Windows 上本项目把 `torchaudio.load` 补丁为 soundfile 实现（`app/backends/gsv.py`），torchaudio ≥ 2.9 无需安装 FFmpeg DLL
- 发布自己的音色包：`$PY scripts/pack_voices.py`
