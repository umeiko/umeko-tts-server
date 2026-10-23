import threading
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.backends.base import BaseBackend
from app.config import Settings
from app.main import create_app
from conftest import TOKEN


class ControlledBackend(BaseBackend):
    name = "controlled-test"
    requires_files = False
    max_concurrency = 4

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()
        self.order = []
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def synthesize(self, job):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.order.append(job.text)
            self.started.set()
        try:
            job.report_progress("loading_model")
            job.report_progress("synthesizing")
            if job.text.startswith("hold"):
                assert self.gate.wait(5), "test backend was not released"
            if job.text == "failure":
                raise RuntimeError("sensitive internal path must not be returned")
            return b"\0\0" * 320, 32000
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def queue_service(tmp_path):
    backend = ControlledBackend()
    app = create_app(
        Settings(data_dir=tmp_path, backend="mock", admin_token=TOKEN, workers=4),
        backend,
    )
    app.state.registry.create("test")
    with TestClient(app) as c:

        class Service:
            admin = {"X-Admin-Token": TOKEN}

            def user(self, name, **policy):
                u = c.post(
                    "/api/users", headers=self.admin, json={"name": name, **policy}
                ).json()
                key = c.post(
                    "/api/keys", headers=self.admin, json={"user_id": u["id"]}
                ).json()
                return u, key, {"X-API-Key": key["key"]}

            def submit(self, headers, text, **extras):
                return c.post(
                    "/v1/jobs", headers=headers, json={"text": text, **extras}
                )

            def wait(self, headers, jid):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    r = c.get(f"/v1/jobs/{jid}", headers=headers).json()
                    if r["state"] in ("completed", "failed", "cancelled", "expired"):
                        return r
                    time.sleep(0.01)
                raise AssertionError("job did not finish")

            def configure(self, **values):
                cfg = {
                    "concurrency": 1,
                    "max_queue": 64,
                    "queue_timeout": 120,
                    "aging_seconds": 15,
                    **values,
                }
                assert (
                    c.put("/api/settings", json=cfg, headers=self.admin).status_code
                    == 200
                )

        service = Service()
        service.client, service.app, service.backend = c, app, backend
        try:
            yield service
        finally:
            backend.gate.set()


def test_priority_fifo_and_cannot_self_promote(queue_service):
    s = queue_service
    _, _, normal = s.user("Normal", priority=0)
    _, _, high = s.user("High", priority=10)
    first = s.submit(normal, "hold").json()
    assert s.backend.started.wait(1)
    low = s.submit(normal, "low", priority=999).json()
    hi1 = s.submit(high, "high-1").json()
    hi2 = s.submit(high, "high-2").json()
    s.backend.gate.set()
    for h, j in [(normal, first), (normal, low), (high, hi1), (high, hi2)]:
        assert s.wait(h, j["id"])["state"] == "completed"
    assert s.backend.order == ["hold", "high-1", "high-2", "low"]
    assert s.backend.peak == 1


def test_aging_prevents_starvation(queue_service):
    s = queue_service
    s.configure(aging_seconds=1)
    _, _, low = s.user("Low", priority=0)
    _, _, high = s.user("High", priority=10)
    s.submit(high, "hold")
    assert s.backend.started.wait(1)
    old = s.submit(low, "older").json()
    s.submit(high, "newer")
    s.client.portal.call(
        lambda: setattr(
            s.app.state.engine.jobs[old["id"]], "queued_at", time.monotonic() - 11
        )
    )
    s.backend.gate.set()
    assert s.wait(low, old["id"])["state"] == "completed"
    assert s.backend.order[:2] == ["hold", "older"]


def test_user_concurrency_and_cancelled_running_slot_remain_occupied(queue_service):
    s = queue_service
    s.configure(concurrency=2)
    _, _, a = s.user("A", max_concurrent=1)
    _, _, b = s.user("B", max_concurrent=1)
    hold = s.submit(a, "hold-a").json()
    assert s.backend.started.wait(1)
    after = s.submit(a, "after-a").json()
    other = s.submit(b, "hold-b").json()
    deadline = time.monotonic() + 1
    while len(s.backend.order) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert s.backend.order == ["hold-a", "hold-b"]
    assert (
        s.client.delete(f"/v1/jobs/{hold['id']}", headers=a).json()["state"]
        == "cancelling"
    )
    time.sleep(0.05)
    assert "after-a" not in s.backend.order
    assert (
        s.client.get("/api/status", headers=s.admin).json()["engine"]["queue"][
            "running"
        ]
        == 2
    )
    s.backend.gate.set()
    assert s.wait(a, hold["id"])["state"] == "cancelled"
    assert s.wait(a, after["id"])["state"] == "completed"
    assert s.wait(b, other["id"])["state"] == "completed"
    assert s.backend.peak <= 2


def test_queue_limits_and_reservations(queue_service):
    s = queue_service
    s.configure(max_queue=1)
    _, _, a = s.user("A", max_queue=1)
    _, _, b = s.user("B", max_queue=1)
    s.submit(a, "hold")
    assert s.backend.started.wait(1)
    pending = s.submit(b, "waiting").json()
    assert s.submit(b, "another").status_code == 429
    assert s.client.get("/v1/me", headers=b).json()["reserved_chars"] == 7
    assert (
        s.client.delete(f"/v1/jobs/{pending['id']}", headers=b).json()["state"]
        == "cancelled"
    )
    assert s.client.get("/v1/me", headers=b).json()["reserved_chars"] == 0
    assert s.submit(b, "allowed").status_code == 202


def test_revocation_cancels_queue_and_other_users_cannot_access(queue_service):
    s = queue_service
    _, _, a = s.user("A")
    _, key, b = s.user("B")
    _, _, c = s.user("C")
    s.submit(a, "hold")
    assert s.backend.started.wait(1)
    job = s.submit(b, "private").json()
    for method, path in [
        ("GET", f"/v1/jobs/{job['id']}"),
        ("GET", f"/v1/jobs/{job['id']}/audio"),
        ("DELETE", f"/v1/jobs/{job['id']}"),
    ]:
        assert s.client.request(method, path, headers=c).status_code == 404
    assert s.client.delete(f"/api/keys/{key['id']}", headers=s.admin).status_code == 200
    record = s.client.get("/api/queue", headers=s.admin).json()["history"]
    assert next(j for j in record if j["id"] == job["id"])["state"] == "cancelled"
    assert s.app.state.access.users()[1]["reserved_chars"] == 0


def test_queue_timeout_and_backend_failure_refund_quota(queue_service):
    s = queue_service
    _, _, a = s.user("A")
    _, _, b = s.user("B")
    first = s.submit(a, "hold").json()
    assert s.backend.started.wait(1)
    pending = s.submit(b, "timeout").json()
    s.client.portal.call(
        lambda: setattr(
            s.app.state.engine.jobs[pending["id"]], "queued_at", time.monotonic() - 121
        )
    )
    result = s.wait(b, pending["id"])
    assert result["state"] == "expired"
    assert s.client.get("/v1/me", headers=b).json()["reserved_chars"] == 0
    s.backend.gate.set()
    s.wait(a, first["id"])
    failure = s.submit(b, "failure").json()
    failed = s.wait(b, failure["id"])
    assert (
        failed["state"] == "failed" and "sensitive internal path" not in failed["error"]
    )
    usage = s.client.get("/v1/me", headers=b).json()
    assert usage["used_chars"] == usage["reserved_chars"] == 0


def test_audio_ownership_retention_and_no_text_history(queue_service):
    s = queue_service
    _, _, a = s.user("A")
    _, _, b = s.user("B")
    job = s.submit(a, "private text").json()
    assert s.wait(a, job["id"])["state"] == "completed"
    url = f"/v1/jobs/{job['id']}/audio"
    assert s.client.get(url, headers=a).content.startswith(b"RIFF")
    assert s.client.get(url, headers=b).status_code == 404
    assert "private text" not in s.client.get("/api/queue", headers=s.admin).text
    s.client.portal.call(
        lambda: setattr(
            s.app.state.engine.jobs[job["id"]], "finished", time.time() - 601
        )
    )
    assert s.client.get(url, headers=a).status_code == 404


def test_cannot_edit_an_in_use_voice(queue_service):
    s = queue_service
    _, _, a = s.user("A")
    s.submit(a, "hold")
    assert s.backend.started.wait(1)
    assert (
        s.client.put(
            "/api/voices/test", headers=s.admin, data={"description": "changed"}
        ).status_code
        == 409
    )
    assert s.client.delete("/api/voices/test", headers=s.admin).status_code == 409


def test_model_capacity_is_enforced(queue_service):
    s = queue_service
    r = s.client.put(
        "/api/settings",
        headers=s.admin,
        json={
            "concurrency": 5,
            "max_queue": 64,
            "queue_timeout": 120,
            "aging_seconds": 15,
        },
    )
    assert r.status_code == 400


def test_database_failure_closes_admission_and_health(queue_service, monkeypatch):
    s = queue_service
    _, _, a = s.user("A")

    def failed_finish(*args):
        raise sqlite3.OperationalError("simulated disk failure")

    monkeypatch.setattr(s.app.state.access, "finish", failed_finish)
    job = s.submit(a, "test").json()
    assert s.wait(a, job["id"])["state"] == "failed"
    assert s.client.get("/healthz").status_code == 503
    assert s.submit(a, "another").status_code == 503


def test_real_segment_progress_and_queued_position(queue_service):
    s = queue_service
    _, _, a = s.user("Progress")
    _, _, b = s.user("Queued")
    job = s.submit(a, "first。hold-second。", cut_punc="。").json()
    deadline = time.monotonic() + 1
    while len(s.backend.order) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    running = s.client.get(job["status_url"], headers=a).json()
    assert running["state"] == "running"
    assert running["progress"]["segments_completed"] == 1
    assert running["progress"]["segment_percent"] == 50
    assert running["progress"]["segment"] == running["progress"]["segments_total"] == 2
    assert running["progress"]["stage"] == "synthesizing"
    queued = s.submit(b, "waiting").json()
    pending = s.client.get(queued["status_url"], headers=b).json()
    assert pending["position"] == 1 and pending["progress"]["stage"] == "queued"
    assert pending["progress"]["segment_percent"] is None
    admin = s.client.get(f"/api/requests/{job['id']}", headers=s.admin).json()
    assert admin["progress"]["segments_completed"] == 1
    s.backend.gate.set()
    finished = s.wait(a, job["id"])
    assert finished["progress"]["stage"] == "completed"
    assert finished["progress"]["segments_completed"] == 2
    s.wait(b, queued["id"])


def test_single_segment_is_indeterminate_and_cancel_preserves_counts(queue_service):
    s = queue_service
    _, _, a = s.user("Cancel progress")
    job = s.submit(a, "hold-single").json()
    assert s.backend.started.wait(1)
    current = s.client.get(job["status_url"], headers=a).json()
    assert current["progress"]["segment_percent"] is None
    assert current["progress"]["segments_completed"] == 0
    s.client.delete(job["status_url"], headers=a)
    s.backend.gate.set()
    final = s.wait(a, job["id"])
    assert final["state"] == final["progress"]["stage"] == "cancelled"


def test_progress_stops_at_output_duration_limit(queue_service):
    s = queue_service
    _, _, a = s.user("Output limit")
    s.backend.synthesize = lambda job: (b"\0\0" * 32000, 32000)
    job = s.submit(a, "one。two。three。", cut_punc="。", max_sec=0.5).json()
    final = s.wait(a, job["id"])
    assert final["state"] == "completed" and final["progress"]["truncated"]
    assert final["progress"]["segments_completed"] == 1
    assert final["progress"]["segments_total"] == 3
    assert final["progress"]["output_seconds"] == 0.5
