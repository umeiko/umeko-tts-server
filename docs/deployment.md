# 可信用户服务与 ALB 部署

## 升级与首次登录

v2 保留 `/tts` 的请求体与 WAV 响应，但不再开放匿名合成或匿名管理。
先备份 `data/`，更新依赖，设置至少 24 字符的随机 `TTS_ADMIN_TOKEN`，再启动。

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 将生成的值保存到部署系统的密钥配置中，不要提交到仓库。
export TTS_ADMIN_TOKEN='<生成的值>'
export TTS_BACKEND=gsv
export UMEKO_TTS_BASE_URL='https://tts.example.com/services/umeko/tts'
python -m app.main
```

PowerShell 对应写法为 `$env:TTS_ADMIN_TOKEN='…'` 与 `$env:UMEKO_TTS_BASE_URL='…'`。
`.env.example` 是配置清单；应用读取进程环境，不会自动加载 `.env` 文件。

打开 `https://tts.example.com/services/umeko/tts/console/`，使用管理员令牌登录。
在“用户与密钥”创建用户，设置优先级、并发、排队、频率和字符额度，再发放 API Key。
完整 Key 只返回一次；管理员列表仅显示前缀。丢失时轮换或重新发放，不支持找回。

管理员登录使用 HttpOnly、SameSite=Strict 会话 Cookie 和写操作 CSRF 校验。
HTTPS 外部地址会启用 Secure Cookie。浏览器不保存管理员令牌或完整 API Key 到 localStorage。
管理员自动化仍可使用 `X-Admin-Token`；普通调用者使用 `Authorization: Bearer <API_KEY>` 或 `X-API-Key`。
API Key 不能访问管理接口，不能读取或取消其他用户的任务。多把 Key 共享所属用户的额度。

修改 `TTS_ADMIN_TOKEN` 并重启会使旧管理员会话失效；用户 API Key 保持有效，除非主动撤销或过期。
未设置管理员令牌时，管理接口返回 503；匿名合成始终返回 401。模型缺失不会自动输出演示音。
`TTS_BACKEND=mock` 仅供明确选择的本地测试。

## 外部 URL、转发路径与前缀

`UMEKO_TTS_BASE_URL` 是唯一的外部地址配置：

| 配置 | 外部控制台 | 外部合成接口 |
|---|---|---|
| 空或 `/` | `/console/` | `/tts` |
| `/services/voice` | `/services/voice/console/` | `/services/voice/tts` |
| `https://tts.example.com/services/voice/` | 同上，并固定公开 origin | 同上 |

完整 URL 推荐用于 HTTPS/ALB 部署；末尾斜线会被归一化。
不接受含账号密码、查询参数、fragment、重复斜线或 `..` 的配置。
应用不会根据任意客户端的 `X-Forwarded-Prefix` 改变地址，也不需要重复传入 Uvicorn `--root-path`。

以 `/services/umeko/tts` 为例：

| 链路 | 浏览器请求 | ALB/NGINX 转发给 9880 | 应用路由 |
|---|---|---|---|
| 保留前缀 | `/services/umeko/tts/api/users` | `/services/umeko/tts/api/users` | `/api/users` |
| 裁去前缀 | `/services/umeko/tts/api/users` | `/api/users` | `/api/users` |

应用同时接受这两种上游路径，并将 ASGI `root_path` 统一为配置前缀。
HTML 中的 JS/CSS、页面中的所有请求、重定向、任务返回地址与 Cookie Path 均使用同一前缀，且只添加一次。
源站的无前缀路由兼容代理裁剪；它们具有同样的鉴权要求。请通过安全组/防火墙限制源站仅接受可信代理访问。
不要让 ALB 把此服务的内部无前缀路由匹配到其他公开服务。

不同云厂商的 ALB 转发规则和重写能力不同，应检查实际规则。
[AWS ALB 支持可选 URL rewrite transform](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/rule-transforms.html)；没有匹配到重写规则时保留原请求。
这份实现兼容保留或裁剪，不要求为本服务额外引入重写。
[FastAPI 的代理说明](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)解释了外部路径与 ASGI root_path 的关系。

### NGINX 示例

以下规则保留路径；若 `proxy_pass` 改为 `http://127.0.0.1:9880/`，则裁去 location 前缀，两种方式均可。
`location` 和公开地址必须一致，包含末尾斜线。

```nginx
location = /services/umeko/tts {
    return 308 /services/umeko/tts/;
}
location /services/umeko/tts/ {
    proxy_pass http://127.0.0.1:9880;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 512m;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

如果 NGINX 位于 HTTPS ALB 后面，`$scheme` 可能为内部 HTTP。
应在仅可信 ALB 能访问的入口上正确传递其外部 scheme，或配置完整的 HTTPS `UMEKO_TTS_BASE_URL` 固定公开 origin。
不要无条件信任公网客户端伪造的代理头。
内置 `python -m app.main` 入口通过 `TTS_FORWARDED_ALLOW_IPS` 指定可信代理 IP/CIDR，默认只有 127.0.0.1。
自定义 Uvicorn 启动命令应设置对应的 `--proxy-headers --forwarded-allow-ips`，避免无条件使用 `*`。

## 长任务与空闲超时

多人排队或 CPU 推理可超过一个 HTTP 连接的空闲超时。
例如 [AWS ALB 默认 idle timeout 为 60 秒](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html#connection-idle-timeout)，其他厂商应查看其实际配置。
首选异步接口，浏览器面板也使用它：

```bash
BASE='https://tts.example.com/services/umeko/tts'
# UMEKO_TTS_API_KEY 由管理员发放，设置到客户端环境中。
curl -X POST "$BASE/v1/jobs" \
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，欢迎使用语音合成服务。","max_sec":10}'

# 保存返回的 id，每 1–3 秒查询；每次均携带同一用户的有效密钥。
curl "$BASE/v1/jobs/<id>" -H "Authorization: Bearer $UMEKO_TTS_API_KEY"
curl "$BASE/v1/jobs/<id>/audio" \
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" -o output.wav
curl -X DELETE "$BASE/v1/jobs/<id>" -H "Authorization: Bearer $UMEKO_TTS_API_KEY"
```

创建返回 202、`Location`、`status_url`；完成后状态为 `completed`，返回 `audio_url`。
状态还包括 `queued`、`running`、`cancelling`、`cancelled`、`failed`、`expired`。
队列位置是当前优先级排序位置；实际开始时间还受每用户并发限制。
结果保存在内存，默认保留 600 秒，总预算 64 MiB；预算满时按最旧结果淘汰，客户端应及时下载。
重启会中断未完成任务并释放其预留额度，旧任务地址不再有效。已完成调用的用量和审计记录仍保留在 SQLite。

`POST /tts` 仍同步返回 WAV，适用于短请求。其连接超时应覆盖排队和推理时间；异步接口通常更合适。
429 表示用户频率、额度或排队上限；排队数量/频率限制包含 `Retry-After`。
503 表示服务未就绪、正在停止或排队超时。400/401/403/404 分别表示输入、凭证、权限/CSRF、资源问题。

### 指定音色

先查询当前可用音色，取返回数组中完整的 `name`（包括英文大小写）；`is_default: true` 标记默认音色。
中文名称直接放在 JSON 里，无需 URL 编码。下列 Bash 示例中的“播音女声”需替换为实际返回的名称。

```bash
curl "$BASE/v1/voices" -H "Authorization: Bearer $UMEKO_TTS_API_KEY"
curl "$BASE/tts" \
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"voice":"播音女声","text":"你好，这是指定音色的测试。","text_lang":"zh","speed_factor":1.0}' \
  -o output.wav
# 异步提交时使用完全相同的 voice 字段。
curl "$BASE/v1/jobs" \
  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"voice":"播音女声","text":"这是一段异步合成的文本。"}'
```

省略 `voice` 或传 `null` 使用管理员设置的默认音色；`text_lang` 和 `speed_factor` 分别控制语言和语速，不选择音色。
`voice` 接受名称，不接受本机权重路径。不存在的名称返回 404，文件未准备好的音色返回 400；`/v1/voices` 只列出可用音色。
网页“接入文档”可选择当前音色，自动更新带实际服务前缀的两种 curl 示例。

### 进度与管理员错误详情

- `GET /v1/jobs/{id}` 增加 `progress`、`elapsed_seconds`（含排队）和 `run_seconds`（推理开始后）。
  `progress.stage` 区分 `queued`、`starting`、`loading_model`、`synthesizing`、`encoding` 和最终状态。
- `segments_completed` / `segments_total` 是引擎实际分段计数，`segment` 是当前段号；
  使用 `cut_punc`（例如 `。！？`）指定外层切分时，多段任务提供 `segment_percent`。
  默认模型内部自动切分没有暴露逐步进度，单段时该百分比为 `null`，网页显示进行中动画，不能据此估计剩余时间。
  `output_seconds` 是已生成音频时长，`truncated` 表示是否达到输出时长限制而提前结束。
- 管理员在“调用记录”或总览的最近记录点击任务号展开；详情包含音色、时间、原始异常类型和 Python 异常链，可复制报错。
  自动刷新保留展开状态。`GET /api/requests/{id}` 按需读取详情，轮询列表不携带大段异常栈。
- 异常栈保存到 SQLite，随记录保留 30 天，音频过期或服务重启后仍能查看；升级自动创建诊断表，旧记录无法补回当时未保存的错误。
  只对管理员开放，普通 API Key 即使属于任务创建者也不能读取；普通合成接口仍返回通用错误与任务号。
- 诊断保留异常链、源码位置和异常消息，不采集 Python 局部变量、请求头或整个进程输出；
  自动遮蔽本服务管理员令牌和匹配格式的 API Key，单条异常栈限制为约 64K 字符。
  第三方异常消息可能包含路径或其自行打印的输入内容，应按管理员诊断资料管理。
  GPT-SoVITS 非流式生成器会被完整消费，以捕获“先返回静音、后抛异常”的失败并完成模型清理。

## 调度与资源边界

- 全局并行数与每用户并行数分别限制，同时满足才分配推理名额。
- GPT-SoVITS 共享一份会切换权重的管线，安全容量为 1；面板拒绝超过当前后端容量的配置。线程数不是可并行模型副本数。
- 用户优先级为 0–10，大者优先；同级 FIFO。每等待 `aging_seconds` 秒提升 1 级，避免低优先级持续饥饿。
- 管理员可在线修改全局并发、等待上限、排队超时与提升间隔。首次启动取环境变量，后续取数据库已保存值。降低并发不会强杀已运行的推理。
- 请求被接受时预留输入字符额度，成功时结算；失败、排队超时、取消释放预留。已接受请求仍计入每分钟限额，防止反复取消绕过限流。每日额度按 UTC 日界线计算。
- 撤销/轮换密钥、停用用户会取消其未完成任务。排队任务立即释放额度；运行任务标记 `cancelling`，实际模型调用结束后才释放占用。
- Python 线程不能被安全强杀。`max_sec` 限制输出长度，不能保证在此秒数内中断模型；文本上限、请求频率和有界队列共同约束资源。
- 活跃音色禁止替换或删除；上传先写临时文件再替换，失败不先删除旧权重。仅管理员可上传，模型文件必须来自可信来源。

### 单实例约束

此版本是一个 SQLite 控制平面和一个内存调度器，**运行一个应用实例、一个 ASGI worker、一个 ALB target**。
同一数据目录有操作系统文件锁，误启第二个调度进程会失败；`WEB_CONCURRENCY>1` 会被拒绝。
多个独立数据目录或多台主机不会共享用户、额度或队列，不能把这些实例直接挂到同一用户服务上来宣称全局限流。
多模型副本需要后续引入共享调度/持久任务存储。当前不使用 sticky routing 来掩盖这个限制。

## 数据、运维与验收

- 用户、Key 摘要、会话摘要、调用计数、额度和调度配置保存在 `TTS_DATA_DIR/access.sqlite3`；Unix 数据库权限为 0600。
- 密钥原文只存在于创建响应；不要把它放入 URL、工单、截图或日志。通过环境变量给客户端传递。
- 调用历史与管理员详情保存 30 天，包括用户、密钥 ID、输入文本、实际生效的合成参数、音色、排队时长、合成耗时、返回音频时长和捕获的异常。输入与参数只通过管理员 `/api/requests/{id}` 按需读取，不放入列表轮询、普通用户状态接口或 MCP 工具结果；不保存请求头、密钥原文或返回音频文件。旧记录无法补回未采集的输入与计时。管理员操作审计最多保留 2000 条。
- 上游模型库可能自行打印推理内容，部署者应控制进程日志的访问与保留策略。
- 停止服务后可备份整个数据目录。运行中备份 SQLite 请使用 SQLite backup API，不能只复制 `.sqlite3` 而遗漏 WAL。
- `/healthz` 供源站健康检查使用，不要求登录，不返回密钥、用户或本机路径。ALB 可配置内部 `/healthz` 或带公开前缀的路径。
- 调度器或状态数据库异常时，健康检查返回 503 并拒绝新任务；修复存储后重启可恢复遗留的字符预留。
- 通过 ALB 实际访问控制台，检查 JS/CSS、用户创建与刷新、发放/轮换/撤销密钥、异步合成、跨用户任务拒绝、注销后读写拒绝和 Cookie Path。
- 本地 TestClient/HTTP 代理或 CI 通过，不等同于已经验证某个线上 ALB 的监听规则、证书、安全组和超时配置。

隔离测试（无需下载模型）：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

测试使用临时数据目录与受控测试后端；不会删除运行中服务的音色。

## 本机 CPU 启动与仁菜音色

完成 README 中的 Python、GPT-SoVITS 依赖和预训练模型安装后：

```powershell
# 单独安装仁菜，保留已安装音色与已有默认音色。
.\.venv\Scripts\python.exe scripts/download_voices.py nina
# 后台启动本机服务；网页、HTTP API 和 MCP 共用一个进程。
.\scripts\start_local_cpu.ps1
# 新机器也可合并安装和启动（不替代前面的运行时安装步骤）：
.\scripts\start_local_cpu.ps1 -InstallNina
```

脚本绑定 `127.0.0.1:9880`，强制真实 CPU 推理；已有环境变量 `GSV_CONFIG` 会被沿用，也可传 `-Config`。
未设置 `TTS_ADMIN_TOKEN` 时，首次生成随机管理员令牌，保存在 `data/admin-token.txt`，限制为当前 Windows 用户访问；后续启动复用该文件。
控制台登录后在“音色与试听”选择 **仁菜**，或在合成请求中传 `"voice":"仁菜"`。
音色包来自 `voices-v1.0.0` 的 `nina.zip`，其中包含权重、参考音频、来源、上游使用说明与校验信息；角色音色不因服务器代码开源而取得商业授权。

调用详情中的 `queue_seconds` 使用单调时钟测量进入队列到获得推理名额的时长；
`synthesis_seconds` 测量实际推理线程执行时间，包含权重切换、模型推理和 WAV 封装，不包含排队和客户端下载。
`audio_seconds` 由返回 PCM 帧数与采样率计算，失败或取消且没有返回音频时为 `null`。
输入按实际用于合成的规范化参数记录，升级自动建立补充表；已有用户、密钥、历史与额度继续保留。

## MCP 接入

同一服务增加 **Streamable HTTP** 入口：`<UMEKO_TTS_BASE_URL>/mcp`（本机为 `http://127.0.0.1:9880/mcp`）。
每次请求均携带现有 API Key。它是预先发放密钥的接入方式，不实现 OAuth 登录发现流程；客户端需支持配置自定义请求头。
配置示例（不同客户端字段名称可能不同，以其 HTTP MCP 配置为准）：

```json
{
  "mcpServers": {
    "umeko-tts": {
      "type": "http",
      "url": "http://127.0.0.1:9880/mcp",
      "headers": {"Authorization": "Bearer <API_KEY>"}
    }
  }
}
```

把占位符替换为管理员发放的密钥。工具包括：

| 工具 | 用途 |
|---|---|
| `list_voices` | 查询可用音色名称，例如“仁菜” |
| `get_my_limits` | 查询当前用户额度、频率和优先级 |
| `submit_synthesis` | 参数与 `/tts` 一致，立即返回任务 ID，不保持长推理连接 |
| `get_job` | 按 `job_id` 查询自己的进度、排队与合成时长 |
| `get_audio` | 完成后按 `job_id` 返回 MCP `audio/wav` 音频内容 |
| `cancel_job` | 取消自己的排队或运行任务 |

客户端先 `initialize` / `tools/list`，调用 `submit_synthesis` 时传入 `{"voice":"仁菜","text":"你好。"}`，
随后每 1–3 秒调用 `get_job`，`completed` 后调用 `get_audio`。所有任务复用现有并发、优先级、频率和字符额度，
不能通过 MCP 访问其他用户任务或管理员调用详情；撤销密钥后立即拒绝请求。结果仍遵循音频缓存保留期。
接口使用无状态 POST/JSON 响应，不依赖长连接 SSE 和服务端会话；GET/DELETE 返回 405。
通过代理部署时设置完整的 HTTPS `UMEKO_TTS_BASE_URL`，以配置允许的外部 Host 和 Origin；默认只信任本机 Host。
保留或剥离前缀均使用同一个入口，反向代理不需要额外启动 MCP 端口。
