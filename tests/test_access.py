import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import cookie_name
from conftest import TOKEN


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/status"),
        ("GET", "/api/users"),
        ("POST", "/api/voices"),
        ("GET", "/api/keys"),
        ("PUT", "/api/settings"),
        ("GET", "/api/openapi"),
        ("POST", "/tts"),
        ("POST", "/v1/jobs"),
        ("GET", "/v1/me"),
        ("GET", "/v1/voices"),
    ],
)
def test_anonymous_is_rejected_before_body_parse(service, method, path):
    response = service.client.request(
        method,
        path,
        content=b"invalid JSON",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert "message" in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_admin_is_closed_when_unconfigured(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, backend="mock"))
    with TestClient(app) as client:
        assert not client.get("/api/auth/session").json()["configured"]
        assert client.get("/api/status").status_code == 503
        assert client.post("/tts", json={"text": "hello"}).status_code == 401
        assert client.get("/healthz").json() == {"status": "ok"}


def test_cookie_csrf_logout_and_no_secret_storage(service):
    c = service.client
    result = c.post("/api/auth/login", json={"token": TOKEN})
    assert result.status_code == 200
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    csrf = result.json()["csrf_token"]
    assert c.get("/api/status").status_code == 200
    assert c.post("/api/users", json={"name": "CSRF attempt"}).status_code == 403
    assert (
        c.post(
            "/api/users",
            json={"name": "CSRF attempt"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/users", json={"name": "Session user"}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/status").status_code == 401
    with sqlite3.connect(service.settings.data_dir / "access.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert (
        TOKEN.encode()
        not in (service.settings.data_dir / "access.sqlite3").read_bytes()
    )


def test_cross_origin_login_and_bruteforce_limit(service):
    c = service.client
    assert (
        c.post(
            "/api/auth/login",
            json={"token": TOKEN},
            headers={"Origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    for _ in range(5):
        assert c.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"


def test_key_is_one_time_and_cannot_administer(service):
    u = service.user()
    assert (
        service.client.get("/v1/me", headers=u.headers).json()["user"] == u.user["name"]
    )
    assert service.client.get("/api/status", headers=u.headers).status_code == 401
    keys = service.client.get("/api/keys", headers=service.admin).json()
    assert "key" not in keys[0] and "digest" not in keys[0]
    assert u.key["key"] not in json.dumps(keys)
    with service.app.state.access.tx() as db:
        saved = db.execute("SELECT digest FROM api_keys").fetchone()[0]
        assert len(saved) == 64 and saved != u.key["key"]


def test_key_rotation_revoke_expiry_and_disable(service):
    u = service.user()
    c = service.client
    r = c.post(f"/api/keys/{u.key['id']}/rotate", headers=service.admin)
    assert r.status_code == 200
    key = r.json()
    assert c.get("/v1/me", headers=u.headers).status_code == 401
    new_headers = {"X-API-Key": key["key"]}
    assert c.get("/v1/me", headers=new_headers).status_code == 200
    with service.app.state.access.tx() as db:
        db.execute(
            "UPDATE api_keys SET expires=? WHERE id=?", (time.time() - 1, key["id"])
        )
    assert c.get("/v1/me", headers=new_headers).status_code == 401
    fresh = c.post(
        "/api/keys", headers=service.admin, json={"user_id": u.user["id"]}
    ).json()
    assert (
        c.delete(f"/api/keys/{fresh['id']}", headers=service.admin).status_code == 200
    )
    assert c.get("/v1/me", headers={"X-API-Key": fresh["key"]}).status_code == 401
    policy = {
        k: u.user[k]
        for k in (
            "name",
            "priority",
            "max_concurrent",
            "max_queue",
            "rpm",
            "daily_chars",
            "max_text_chars",
        )
    }
    assert (
        c.put(
            f"/api/users/{u.user['id']}",
            headers=service.admin,
            json={**policy, "enabled": False},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/keys", headers=service.admin, json={"user_id": u.user["id"]}
        ).status_code
        == 400
    )


def test_limits_shared_across_keys_and_charge_only_success(service):
    u = service.user(daily_chars=5, rpm=5, max_text_chars=4)
    c = service.client
    assert c.post("/tts", headers=u.headers, json={"text": "12345"}).status_code == 400
    r = c.post("/tts", headers=u.headers, json={"text": "1234"})
    assert r.status_code == 200 and r.content.startswith(b"RIFF")
    second = c.post(
        "/api/keys", headers=service.admin, json={"user_id": u.user["id"]}
    ).json()
    headers = {"X-API-Key": second["key"]}
    assert c.post("/tts", headers=headers, json={"text": "12"}).status_code == 429
    assert c.get("/v1/me", headers=headers).json()["used_chars"] == 4


def test_rpm_shared_across_keys(service):
    u = service.user(rpm=1)
    c = service.client
    assert c.post("/tts", headers=u.headers, json={"text": "a"}).status_code == 200
    second = c.post(
        "/api/keys", headers=service.admin, json={"user_id": u.user["id"]}
    ).json()
    r = c.post("/tts", headers={"X-API-Key": second["key"]}, json={"text": "b"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_finite_parameters_and_body_limit(service):
    u = service.user()
    c = service.client
    assert (
        c.post(
            "/tts", headers=u.headers, json={"text": "a", "max_sec": "NaN"}
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/tts", headers=u.headers, json={"text": "a", "temperature": "Infinity"}
        ).status_code
        == 400
    )
    r = c.post(
        "/tts",
        headers={**u.headers, "Content-Type": "application/json"},
        content=b"x" * (256 * 1024 + 1),
    )
    assert r.status_code == 413
    r = c.post(
        "/tts",
        headers={**u.headers, "Content-Type": "application/json"},
        content=iter([b"x" * (128 * 1024), b"x" * (129 * 1024)]),
    )
    assert r.status_code == 413


def test_voice_crud_upload_is_atomic_and_authenticated(service):
    c = service.client
    headers = service.admin
    files = {
        "gpt_file": ("../../model.ckpt", b"old trusted fixture"),
        "sovits_file": ("model.pth", b"trusted sovits fixture"),
        "ref_audio": ("reference.wav", b"RIFF test fixture"),
    }
    path = "/api/voices"
    response = c.post(path, headers=headers, data={"name": "Upload_Test"}, files=files)
    assert response.status_code == 200 and response.json()["is_complete"]
    voice_dir = service.app.state.registry.voice_dir("Upload_Test")
    original = (voice_dir / "gpt_weights.ckpt").read_bytes()
    response = c.put(
        path + "/Upload_Test", headers=headers, files={"gpt_file": ("empty.ckpt", b"")}
    )
    assert response.status_code == 400
    assert (voice_dir / "gpt_weights.ckpt").read_bytes() == original
    assert not list(voice_dir.glob(".upload-*"))
    assert c.get(path + "/Upload_Test/ref-audio").status_code == 401
    assert c.get(path + "/Upload_Test/ref-audio", headers=headers).content.startswith(
        b"RIFF"
    )
    assert (
        c.put(
            path + "/Upload_Test",
            headers=headers,
            data={"description": "Updated fixture"},
        ).json()["description"]
        == "Updated fixture"
    )
    assert c.post(path + "/Upload_Test/default", headers=headers).status_code == 200
    assert c.delete(path + "/Upload_Test", headers=headers).status_code == 200
    assert not voice_dir.exists()


def test_path_only_base_still_sets_secure_cookie_on_https(tmp_path):
    settings = Settings(
        data_dir=tmp_path, backend="mock", admin_token=TOKEN, base_url="/voice"
    )
    with TestClient(
        create_app(settings), base_url="https://tts.example.test"
    ) as client:
        response = client.post("/voice/api/auth/login", json={"token": TOKEN})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]


def test_persistence_and_restart_recovery(tmp_path):
    settings = Settings(data_dir=tmp_path, backend="mock", admin_token=TOKEN)
    admin = {"X-Admin-Token": TOKEN}
    app = create_app(settings)
    app.state.registry.create("example")
    with TestClient(app) as c:
        user = c.post(
            "/api/users", json={"name": "Persisted user"}, headers=admin
        ).json()
        key = c.post("/api/keys", json={"user_id": user["id"]}, headers=admin).json()
        c.put(
            "/api/settings",
            json={
                "concurrency": 1,
                "max_queue": 7,
                "queue_timeout": 60,
                "aging_seconds": 3,
            },
            headers=admin,
        )
        assert (
            c.post(
                "/tts", json={"text": "abc"}, headers={"X-API-Key": key["key"]}
            ).status_code
            == 200
        )
        assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
        saved_cookie = c.cookies.get(cookie_name(settings))
        app.state.access.reserve(
            "orphan-request", app.state.access.authenticate(key["key"]), 5
        )
    with TestClient(create_app(settings)) as c:
        assert (
            c.get("/v1/me", headers={"X-API-Key": key["key"]}).json()["used_chars"] == 3
        )
        assert (
            c.get("/v1/me", headers={"X-API-Key": key["key"]}).json()["reserved_chars"]
            == 0
        )
        assert (
            next(
                r
                for r in c.get("/api/queue", headers=admin).json()["history"]
                if r["id"] == "orphan-request"
            )["state"]
            == "interrupted"
        )
        assert (
            c.get("/api/status", headers=admin).json()["engine"]["config"]["max_queue"]
            == 7
        )
        c.cookies.set(cookie_name(settings), saved_cookie)
        assert c.get("/api/status").status_code == 200
    changed = Settings(data_dir=tmp_path, backend="mock", admin_token=TOKEN + "rotated")
    with TestClient(create_app(changed)) as c:
        c.cookies.set(cookie_name(changed), saved_cookie)
        assert c.get("/api/status").status_code == 401


def test_no_secret_or_text_in_audit_or_history(service):
    u = service.user()
    text = "private input text"
    assert (
        service.client.post("/tts", json={"text": text}, headers=u.headers).status_code
        == 200
    )
    for endpoint in ("/api/queue", "/api/audit"):
        body = service.client.get(endpoint, headers=service.admin).text
        assert text not in body and u.key["key"] not in body and TOKEN not in body
