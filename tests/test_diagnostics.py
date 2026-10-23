import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.access import AccessStore
from app.backends.base import BackendError, BaseBackend, SynthesisJob
from app.backends.gsv import GSVBackend
from app.config import Settings
from app.main import create_app
from conftest import TOKEN


def wait_job(client, headers, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
        if result["state"] in {"failed", "completed"}:
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class FailureBackend(BaseBackend):
    requires_files = False

    def synthesize(self, job):
        try:
            raise FileNotFoundError("/synthetic/model/weights.pth")
        except FileNotFoundError as exc:
            raise BackendError(
                f"model failed; token={TOKEN}; utts_" + "S" * 43
            ) from exc


def test_traceback_admin_only_persists_across_pruning_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, backend="mock", admin_token=TOKEN)
    app = create_app(settings, FailureBackend())
    app.state.registry.create("旁白")
    admin = {"X-Admin-Token": TOKEN}
    with TestClient(app) as c:
        user = c.post(
            "/api/users", headers=admin, json={"name": "Synthetic user"}
        ).json()
        key = c.post("/api/keys", headers=admin, json={"user_id": user["id"]}).json()
        auth = {"X-API-Key": key["key"]}
        job = c.post(
            "/v1/jobs", headers=auth, json={"text": "private request", "voice": "旁白"}
        ).json()
        jid = job["id"]
        public = wait_job(c, auth, jid)
        assert public["state"] == "failed"
        assert "FileNotFoundError" not in json.dumps(public)
        assert "traceback" not in public and "error_type" not in public
        assert c.get(f"/api/requests/{jid}").status_code == 401
        assert c.get(f"/api/requests/{jid}", headers=auth).status_code == 401
        detail = c.get(f"/api/requests/{jid}", headers=admin).json()
        trace = detail["traceback"]
        assert "Traceback (most recent call last)" in trace
        assert "FileNotFoundError" in trace and "BackendError" in trace
        assert "direct cause" in trace and "/synthetic/model/weights.pth" in trace
        assert TOKEN not in trace and ("utts_" + "S" * 43) not in trace
        assert "private request" not in json.dumps(detail)
        assert detail["voice"] == "旁白" and detail["started"]
        history = c.get("/api/queue", headers=admin).json()["history"]
        assert history[0]["has_traceback"] == 1
        assert "Traceback (most recent call last)" not in json.dumps(history)
        c.portal.call(lambda: app.state.engine.jobs.pop(jid))
        assert c.get(f"/api/requests/{jid}", headers=admin).json()["traceback"] == trace
    with TestClient(create_app(settings)) as c:
        saved = c.get(f"/api/requests/{jid}", headers=admin).json()
        assert saved["traceback"] == trace and saved["progress"]["stage"] == "failed"
        assert saved["run_seconds"] >= 0
        assert c.get("/api/requests/unknown", headers=admin).status_code == 404


def test_legacy_history_upgrade_and_retention_cascade(tmp_path):
    # Emulate the pre-diagnostics schema and a retained failure with no traceback.
    with sqlite3.connect(tmp_path / "access.sqlite3") as db:
        db.execute("""CREATE TABLE requests(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
            key_id TEXT,day TEXT NOT NULL,chars INTEGER NOT NULL,state TEXT NOT NULL,
            created REAL NOT NULL,finished REAL)""")
        db.execute(
            "INSERT INTO requests VALUES('old','admin',NULL,'2020-01-01',1,'failed',1,2)"
        )
    store = AccessStore(tmp_path)
    try:
        old = store.request_detail("old")
        assert not old["diagnostics_available"] and old["traceback"] is None
        assert store.request_history()[0]["has_traceback"] == 0
        from app.access import Principal

        store.reserve("new", Principal.admin(), 1, "test")
        store.finish("new", "failed", {"traceback": "fixture error"})
        with store.tx() as db:
            db.execute("DELETE FROM requests WHERE id='new'")
            assert (
                db.execute("SELECT COUNT(*) FROM request_diagnostics").fetchone()[0]
                == 0
            )
    finally:
        store.close()


def synthesis_job():
    return SynthesisJob("hello", "en", 1, 15, 1, 1, 10, "test")


@pytest.mark.parametrize("fails", [True, False])
def test_gsv_consumes_generator_cleanup_and_late_errors(fails):
    events = []

    class Pipeline:
        def run(self, request):
            assert request["return_fragment"] is False
            try:
                yield 32000, b"audio"
                if fails:
                    raise RuntimeError("late upstream exception after silence")
            finally:
                events.append("cleanup")

    backend = GSVBackend(None)
    backend._pipeline = Pipeline()
    backend._ensure_weights = lambda job: None
    backend._to_pcm16 = lambda audio: audio
    job = synthesis_job()
    job.on_progress = events.append
    if fails:
        with pytest.raises(BackendError, match="late upstream exception") as error:
            backend.synthesize(job)
        assert isinstance(error.value.__cause__, RuntimeError)
    else:
        assert backend.synthesize(job) == (b"audio", 32000)
    assert events == ["loading_model", "synthesizing", "cleanup"]


def test_gsv_rejects_empty_generator():
    class Pipeline:
        def run(self, request):
            return iter(())

    backend = GSVBackend(None)
    backend._pipeline = Pipeline()
    backend._ensure_weights = lambda job: None
    with pytest.raises(BackendError, match="模型未返回音频"):
        backend.synthesize(synthesis_job())
