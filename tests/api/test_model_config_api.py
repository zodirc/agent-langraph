from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.runtime_model_config import get_runtime_model_config


def test_model_config_get_put_reset(monkeypatch):
    import app.config.settings as settings_module

    monkeypatch.setattr(settings_module.settings, "AUTH_ENABLED", False)
    store = get_runtime_model_config()
    store.reset()

    client = TestClient(app)
    initial = client.get("/model/config")
    assert initial.status_code == 200
    body = initial.json()
    assert "provider" in body
    assert "providers" in body
    assert body["api_key_configured"] in (True, False)

    updated = client.put(
        "/model/config",
        json={
            "provider": "deepseek",
            "model_name": "deepseek-chat",
            "api_key": "sk-test-key",
            "enabled": True,
        },
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["provider"] == "deepseek"
    assert data["model_name"] == "deepseek-chat"
    assert data["source"] == "runtime"
    assert data["api_key_hint"] == "***-key"

    reset = client.post("/model/config/reset")
    assert reset.status_code == 200
    assert reset.json()["source"] == "env"
