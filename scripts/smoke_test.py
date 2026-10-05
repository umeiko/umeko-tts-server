"""冒烟测试：验证 /tts 合成接口与 /api 管理接口。

用法：先启动服务（mock 后端即可），再执行
    python scripts/smoke_test.py [base_url]
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9880"
PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def req(method: str, path: str, body=None, raw: bool = False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            payload = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, (payload if raw else json.loads(payload)), headers
    except urllib.error.HTTPError as e:
        payload = e.read()
        headers = {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(payload), headers
        except json.JSONDecodeError:
            return e.code, payload, headers


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = f"----umeko{time.time_ns()}"
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    for k, (filename, content) in files.items():
        buf.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8")
        )
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def req_form(method: str, path: str, fields: dict, files: dict):
    data, ctype = multipart(fields, files)
    r = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": ctype}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def main():
    print(f"目标服务: {BASE}\n")

    print("[1] 服务状态")
    code, s, _ = req("GET", "/api/status")
    check("GET /api/status", code == 200 and "engine" in s, f"-> {code} {s}")

    print("[2] 清理历史测试音色")
    for name in ("冒烟音色", "mambo"):
        req("DELETE", f"/api/voices/{urllib.parse.quote(name)}")
    code, s, _ = req("GET", "/api/voices")
    check("音色列表为空", code == 200 and s == [], f"-> {s}")

    print("[3] 无音色时调用 /tts 应 400 且带 message")
    code, s, _ = req("POST", "/tts", {"text": "你好世界"})
    check("无音色报错", code == 400 and "message" in s, f"-> {code} {s}")

    print("[4] 新增音色（中文名 + 三类文件上传）")
    code, v = req_form(
        "POST", "/api/voices",
        {"name": "冒烟音色", "description": "自动化测试", "ref_text": "这是参考音频的文字。", "ref_lang": "zh"},
        {
            "gpt_file": ("model.ckpt", b"fake-gpt-weights"),
            "sovits_file": ("model.pth", b"fake-sovits-weights"),
            "ref_audio": ("ref.wav", b"RIFF" + b"\x00" * 100),
        },
    )
    check(
        "创建成功且完整",
        code == 200 and v.get("is_complete") and v.get("is_default") and v.get("description") == "自动化测试",
        f"-> {code} {v}",
    )

    print("[5] /tts 最小调用")
    code, data, headers = req("POST", "/tts", {"text": "你好世界"}, raw=True)
    ok = code == 200 and headers.get("content-type") == "audio/wav" and wav_seconds(data) > 0.2
    check("返回可解析 WAV", ok, f"-> {code} {headers.get('Content-Type')} {len(data)}B")

    print("[6] /tts 完整参数 + cut_punc 切分 + max_sec 截断")
    code, data, headers = req(
        "POST", "/tts",
        {
            "text": "第一句。第二句！第三句？",
            "voice": "冒烟音色",
            "text_lang": "zh",
            "speed_factor": 1.2,
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 1.0,
            "cut_punc": "。！？",
            "max_sec": 1.0,
        },
        raw=True,
    )
    secs = wav_seconds(data) if code == 200 else -1
    check("max_sec 截断生效", code == 200 and 0.5 <= secs <= 1.05, f"-> {code} {secs:.2f}s")
    check("响应头含音色与耗时", "x-voice" in headers and "x-inference-ms" in headers, f"-> {headers}")

    print("[7] 参数校验与错误格式")
    cases = [
        ("空文本", {"text": "  "}, 400),
        ("缺 text", {}, 400),
        ("非法语种", {"text": "a", "text_lang": "fr"}, 400),
        ("未知音色", {"text": "a", "voice": "不存在"}, 404),
    ]
    for name, body, expect in cases:
        code, s, _ = req("POST", "/tts", body)
        check(f"{name} -> {expect} + message", code == expect and "message" in s, f"-> {code} {s}")
    # 非法 JSON body 需要原始字节请求
    r = urllib.request.Request(
        BASE + "/tts", data=b"{not json", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(r, timeout=10)
        code, s = 200, {}
    except urllib.error.HTTPError as e:
        code, s = e.code, json.loads(e.read())
    check("非法 JSON -> 400 + message", code == 400 and "message" in s, f"-> {code} {s}")

    print("[8] 并发 8 路请求全部成功（串行锁排队）")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(req, "POST", "/tts", {"text": f"并发测试{i}号"}, True) for i in range(8)]
        results = [f.result() for f in futures]
    ok = all(c == 200 and wav_seconds(d) > 0 for c, d, _ in results)
    check("8 并发全部 200", ok, f"-> {[c for c, _, _ in results]}")
    code, s, _ = req("GET", "/api/status")
    check("队列计数回落", s["engine"]["queue"]["pending"] == 0 and s["engine"]["queue"]["completed"] >= 8, f"-> {s['engine']['queue']}")

    print("[9] 更新 / 默认音色 / 参考音频 / 删除")
    code, v = req_form("PUT", "/api/voices/" + urllib.parse.quote("冒烟音色"), {"description": "更新后的描述"}, {})
    check("PUT 更新描述", code == 200 and v.get("description") == "更新后的描述", f"-> {code} {v}")
    code, s, _ = req("POST", "/api/voices/" + urllib.parse.quote("冒烟音色") + "/default")
    check("设为默认", code == 200 and "message" in s, f"-> {code} {s}")
    code, data, _ = req("GET", "/api/voices/" + urllib.parse.quote("冒烟音色") + "/ref-audio", raw=True)
    check("下载参考音频", code == 200 and data.startswith(b"RIFF"), f"-> {code}")
    code, s, _ = req("DELETE", "/api/voices/" + urllib.parse.quote("冒烟音色"))
    check("删除音色", code == 200 and "message" in s, f"-> {code} {s}")

    print("[10] 控制台页面")
    code, html, _ = req("GET", "/console/", raw=True)
    check("GET /console/ 返回页面", code == 200 and b"Umeko TTS" in html, f"-> {code}")
    code, _, _ = req("GET", "/", raw=True)
    check("GET / 可访问(重定向)", code in (200, 307), f"-> {code}")

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
