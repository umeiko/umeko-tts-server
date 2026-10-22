"""Non-destructive deployment check. No voice creation, replacement or deletion.

Usage: UMEKO_TTS_API_KEY=... python scripts/smoke_test.py https://tts.example.com/prefix
Optional: UMEKO_TTS_CA_FILE for a private CA; TTS_TEST_ADMIN_TOKEN for admin status.
TLS and host verification remain enabled. Secrets are read from the environment, not argv.
"""

from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise RuntimeError(
                "Cross-origin redirect refused; use the canonical HTTPS service URL"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:9880")
    base = parser.parse_args().base_url.rstrip("/")
    url = urllib.parse.urlsplit(base)
    if (
        url.scheme not in ("https", "http")
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise SystemExit(
            "Provide an absolute http(s) base URL, including the deployment prefix"
        )
    key = os.environ.get("UMEKO_TTS_API_KEY", "")
    if not key:
        raise SystemExit(
            "Set UMEKO_TTS_API_KEY to a dedicated test user's key; no requests were sent"
        )
    ctx = ssl.create_default_context(cafile=os.environ.get("UMEKO_TTS_CA_FILE") or None)
    opener = urllib.request.build_opener(
        SameOriginRedirect(), urllib.request.HTTPSHandler(context=ctx)
    )

    def request(method, path, body=None, auth=True, admin=False):
        headers = {"Authorization": "Bearer " + key} if auth else {}
        if admin:
            headers = {"X-Admin-Token": os.environ["TTS_TEST_ADMIN_TOKEN"]}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        with opener.open(
            urllib.request.Request(
                base + path, data=data, headers=headers, method=method
            ),
            timeout=30,
        ) as response:
            return response.status, response.read()

    assert request("GET", "/healthz", auth=False)[0] == 200
    print("Health check passed")
    assert request("GET", "/v1/me")[0] == 200
    assert request("GET", "/v1/voices")[0] == 200
    print("API key and voice discovery passed")
    code, data = request(
        "POST", "/v1/jobs", {"text": "你好，这是服务连通性测试。", "max_sec": 3}
    )
    assert code == 202
    job = json.loads(data)
    path = "/v1/jobs/" + job["id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        _, data = request("GET", path)
        status = json.loads(data)
        if status["state"] == "completed":
            code, audio = request("GET", path + "/audio")
            assert code == 200 and audio.startswith(b"RIFF")
            out = Path(__file__).resolve().parents[1] / "tmp/e2e/output.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(audio)
            print(f"Asynchronous synthesis passed ({len(audio)} bytes); output: {out}")
            break
        if status["state"] in ("failed", "cancelled", "expired"):
            raise RuntimeError("Synthesis ended with state: " + status["state"])
        time.sleep(1)
    else:
        request("DELETE", path)
        raise RuntimeError("Timed out after 600 seconds; cancellation requested")
    if os.environ.get("TTS_TEST_ADMIN_TOKEN"):
        assert request("GET", "/api/status", admin=True)[0] == 200
        print("Admin status check passed")
    print("All deployment checks passed; existing voices were unchanged")


if __name__ == "__main__":
    main()
