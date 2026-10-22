from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN = "test-admin-secret-" + "x" * 32


@pytest.fixture
def service(tmp_path):
    app = create_app(
        Settings(data_dir=tmp_path, backend="mock", admin_token=TOKEN, workers=4)
    )
    app.state.registry.create("样例旁白")
    with TestClient(app) as client:
        admin = {"X-Admin-Token": TOKEN}

        def user(name="Example user", **policy):
            result = client.post(
                "/api/users", headers=admin, json={"name": name, **policy}
            )
            assert result.status_code == 200, result.text
            u = result.json()
            result = client.post("/api/keys", headers=admin, json={"user_id": u["id"]})
            assert result.status_code == 200, result.text
            key = result.json()
            return SimpleNamespace(
                user=u, key=key, headers={"Authorization": "Bearer " + key["key"]}
            )

        yield SimpleNamespace(
            app=app, client=client, admin=admin, user=user, settings=app.state.settings
        )
