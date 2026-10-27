import base64
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import TOKEN


def rpc(c, path, headers, method, params=None):
    return c.post(
        path,
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
            **headers,
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            **({"params": params} if params is not None else {}),
        },
    )


@pytest.mark.parametrize(
    "prefix,strip",
    [
        ("", False),
        ("/speech", False),
        ("/services/umeko/tts", False),
        ("/services/umeko/tts", True),
        ("/api", False),
        ("/api", True),
    ],
)
def test_mcp_initialization_tools_user_isolation_and_prefixes(tmp_path, prefix, strip):
    app = create_app(
        Settings(
            data_dir=tmp_path,
            backend="mock",
            admin_token=TOKEN,
            base_url="https://tts.example.test" + prefix,
        )
    )
    app.state.registry.create("仁菜")
    admin = {"X-Admin-Token": TOKEN}

    def route(path):
        return path if strip else prefix + path

    with TestClient(app, base_url="https://tts.example.test") as c:

        def user(name):
            u = c.post(route("/api/users"), headers=admin, json={"name": name}).json()
            k = c.post(
                route("/api/keys"), headers=admin, json={"user_id": u["id"]}
            ).json()
            return {"Authorization": "Bearer " + k["key"]}, k["id"]

        a, kid = user("MCP A")
        b, _ = user("MCP B")
        path = route("/mcp")
        assert c.post(path, content=b"invalid JSON").status_code == 401
        init = rpc(
            c,
            path,
            a,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "unit-test", "version": "1"},
            },
        )
        assert init.status_code == 200, init.text
        assert init.json()["result"]["serverInfo"]["name"] == "umeko-tts"
        assert "mcp-session-id" not in init.headers
        names = [
            t["name"] for t in rpc(c, path, a, "tools/list").json()["result"]["tools"]
        ]
        assert set(names) == {
            "list_voices",
            "get_my_limits",
            "submit_synthesis",
            "get_job",
            "get_audio",
            "cancel_job",
        }

        def call(auth, name, args={}):
            r = rpc(c, path, auth, "tools/call", {"name": name, "arguments": args})
            assert r.status_code == 200, r.text
            return r.json()["result"]

        assert (
            call(a, "list_voices")["structuredContent"]["voices"][0]["name"] == "仁菜"
        )
        assert call(a, "get_my_limits")["structuredContent"]["user"] == "MCP A"
        job = call(
            a, "submit_synthesis", {"text": "MCP synthetic input", "voice": "仁菜"}
        )["structuredContent"]
        assert job["status_url"].startswith(prefix + "/v1/jobs/")
        for _ in range(100):
            result = call(a, "get_job", {"job_id": job["id"]})
            current = result["structuredContent"]
            if current["state"] == "completed":
                break
            time.sleep(0.01)
        assert current["state"] == "completed"
        assert current["audio_seconds"] > 0 and "request" not in current
        for tool in ["get_job", "get_audio", "cancel_job"]:
            denied = call(b, tool, {"job_id": job["id"]})
            assert denied["isError"] and denied["structuredContent"]["status"] == 404
        audio = call(a, "get_audio", {"job_id": job["id"]})["content"][0]
        assert audio["type"] == "audio" and audio["mimeType"] == "audio/wav"
        assert base64.b64decode(audio["data"]).startswith(b"RIFF")
        assert c.get(path, headers=a).status_code == 405
        assert c.get(route("/api/requests/" + job["id"]), headers=a).status_code == 401
        c.delete(route("/api/keys/" + kid), headers=admin)
        assert rpc(c, path, a, "tools/list").status_code == 401


def test_mcp_rejects_untrusted_host_and_origin(service):
    user = service.user()
    assert rpc(service.client, "/mcp", user.headers, "tools/list").status_code == 421
    headers = {
        "Host": "localhost",
        "Origin": "https://untrusted.example",
        **user.headers,
    }
    assert rpc(service.client, "/mcp", headers, "tools/list").status_code == 403


def test_mcp_validation_and_quota_errors_use_safe_tool_results(service):
    user = service.user(max_text_chars=3)
    headers = {"Host": "localhost", **user.headers}
    result = rpc(
        service.client,
        "/mcp",
        headers,
        "tools/call",
        {"name": "submit_synthesis", "arguments": {"text": "too long"}},
    ).json()["result"]
    assert result["isError"] and result["structuredContent"]["status"] == 400
    result = rpc(
        service.client,
        "/mcp",
        headers,
        "tools/call",
        {"name": "submit_synthesis", "arguments": {"text": "x", "voice": "missing"}},
    ).json()["result"]
    assert result["isError"] and result["structuredContent"]["status"] == 404
