"""公网端到端验证：默认音色 / 指定音色 zero-shot / /api/status。

用法:
    python scripts/public_e2e.py <base_url> [admin_token]

示例:
    python scripts/public_e2e.py https://tts.example.com
    python scripts/public_e2e.py https://tts.example.com <TTS_ADMIN_TOKEN>
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:9880"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def post(path: str, payload: dict, timeout: int = 1200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, time.time() - t0, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, "application/json", e.read()


def get(path: str, timeout: int = 60):
    headers = {"X-Admin-Token": TOKEN} if TOKEN else {}
    req = urllib.request.Request(BASE + path, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, time.time() - t0, r.read()
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read()


def tts_case(name: str, payload: dict, out: str):
    st, dt, ct, data = post("/tts", payload)
    print(f"[{name}] status={st} time={dt:.1f}s bytes={len(data)} ct={ct}", flush=True)
    if st == 200 and data[:4] == b"RIFF":
        with open(out, "wb") as f:
            f.write(data)
        print(f"[{name}] saved -> {out}", flush=True)
    else:
        print(f"[{name}] body: {data[:400]!r}", flush=True)


if __name__ == "__main__":
    st, dt, data = get("/api/status")
    print(f"[status] {st} {dt:.1f}s {data[:300]!r}", flush=True)

    tts_case("default", {"text": "你好世界，这是一段语音合成测试。", "max_sec": 5}, "e2e_default.wav")
    tts_case(
        "ja",
        {"text": "こんにちは。よろしくお願いします。", "text_lang": "ja", "max_sec": 5},
        "e2e_ja.wav",
    )
