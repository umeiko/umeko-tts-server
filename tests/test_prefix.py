import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.process_lock import ProcessLock
from app.registry import VoiceError, VoiceRegistry
from conftest import TOKEN


@pytest.mark.parametrize(
    "prefix", ["", "/speech", "/services/umeko/tts", "/api", "/console"]
)
@pytest.mark.parametrize("strip", [False, True])
def test_proxy_paths_assets_cookies_api_and_job_urls(tmp_path, prefix, strip):
    settings = Settings(
        data_dir=tmp_path,
        backend="mock",
        admin_token=TOKEN,
        base_url="https://tts.example.test" + prefix,
    )
    app = create_app(settings)
    app.state.registry.create("样例音色")

    # Proxy transport strips the public prefix before contacting ASGI, while the browser keeps it.
    def upstream(path):
        return path[len(prefix) :] or "/" if strip and prefix else path

    with TestClient(app, base_url="https://tts.example.test") as c:
        html = c.get(upstream(prefix + "/console/"))
        assert html.status_code == 200
        assert f'content="{prefix}"' in html.text
        for path in re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html.text):
            assert path.startswith(prefix + "/console/")
            assert c.get(upstream(path)).status_code == 200
        r = c.post(
            upstream(prefix + "/api/auth/login"),
            json={"token": TOKEN},
            headers={"Origin": "https://tts.example.test"},
        )
        assert r.status_code == 200
        assert (
            f"Path={prefix}/" in r.headers["set-cookie"]
            and "Secure" in r.headers["set-cookie"]
        )
        # A stripped upstream request still carries the Cookie forwarded by the proxy.
        cookie = r.headers["set-cookie"].split(";", 1)[0]
        h = {
            "Cookie": cookie,
            "X-CSRF-Token": r.json()["csrf_token"],
            "Origin": "https://tts.example.test",
        }
        assert c.get(upstream(prefix + "/api/status"), headers=h).status_code == 200
        u = c.post(
            upstream(prefix + "/api/users"), headers=h, json={"name": "Prefix test"}
        ).json()
        k = c.post(
            upstream(prefix + "/api/keys"), headers=h, json={"user_id": u["id"]}
        ).json()
        key = {"X-API-Key": k["key"]}
        job = c.post(upstream(prefix + "/v1/jobs"), headers=key, json={"text": "hello"})
        assert job.status_code == 202
        assert job.headers["location"].startswith(prefix + "/v1/jobs/")
        assert c.get(upstream(job.json()["status_url"]), headers=key).status_code == 200
        assert c.get(upstream(prefix + "/api/openapi"), headers=h).json()[
            "servers"
        ] == [{"url": settings.base_url}]
        assert (
            c.post(upstream(prefix + "/api/auth/logout"), headers=h).status_code == 200
        )
        assert c.get(upstream(prefix + "/api/status"), headers=h).status_code == 401
        assert c.get(upstream(prefix + "/healthz")).status_code == 200
        assert c.get("/wrong/prefix/api/status", headers=h).status_code == 404
        if prefix:
            assert (
                c.get(
                    prefix + prefix + "/api/status", headers={"X-Admin-Token": TOKEN}
                ).status_code
                == 404
            )


@pytest.mark.parametrize(
    "base",
    [
        "//evil.example",
        "/a//b",
        "/a/../b",
        "/a/%2e%2e/b",
        "/a?b=c",
        "https://user:secret@example.test/a",
        "ftp://example.test/a",
        "relative/path",
        "/a#fragment",
    ],
)
def test_invalid_base_urls_rejected(base):
    with pytest.raises(ValueError):
        Settings(base_url=base).validate()


def test_forwarded_prefix_cannot_override_config(service):
    r = service.client.get("/console/", headers={"X-Forwarded-Prefix": "/evil"})
    assert r.status_code == 200 and 'content="/evil"' not in r.text


def test_single_scheduler_lock(tmp_path):
    first = ProcessLock(tmp_path)
    first.acquire()
    second = ProcessLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_voice_path_escape_and_reserved_name(tmp_path):
    registry = VoiceRegistry(tmp_path)
    for name in ("../outside", "CON", "LPT1"):
        with pytest.raises(VoiceError):
            registry.voice_dir(name)
    with pytest.raises(VoiceError):
        registry.abs_path("../outside")
