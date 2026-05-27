from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.metrics_service import get_metrics_service
from app.services.tenant_context import set_tenant_id
from app.services.tenant_quota import reset_tenant_quota_store, record_usage


def test_create_tenant_requires_multi_tenant(isolated_stores, monkeypatch):
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", False)
    client = TestClient(app)
    resp = client.post(
        "/tenants",
        json={"tenant_id": "acme"},
        headers={"X-User-Role": "admin"},
    )
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"].lower()


def test_create_and_quota_tenant(tmp_path, isolated_stores, monkeypatch):
    db = tmp_path / "db" / "agent.db"
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "POSTGRES_URL", "")
    monkeypatch.setattr(settings, "SQLITE_PATH", str(db))
    monkeypatch.setattr(settings, "VECTORSTORE_PATH", str(tmp_path / "vectorstore"))
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)

    reset_tenant_quota_store()
    client = TestClient(app)

    created = client.post(
        "/tenants",
        json={"tenant_id": "acme"},
        headers={"X-User-Role": "admin"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["tenant_id"] == "acme"
    assert body["backend"] == "sqlite"

    set_tenant_id("acme")
    record_usage("acme", "tokens", 42)
    get_metrics_service().record_llm_cost_usd(0.01, tenant_id="acme", user_id="u1")

    quota = client.get("/tenants/acme/quota", headers={"X-Tenant-Id": "acme"})
    assert quota.status_code == 200
    q = quota.json()
    assert q["usage"]["tokens_today"] == 42
    assert q["llm_cost_usd"] == 0.01

    metrics = client.get("/metrics/tenant", headers={"X-Tenant-Id": "acme"})
    assert metrics.status_code == 200
    assert metrics.json()["tenant_id"] == "acme"

    deleted = client.delete("/tenants/acme", headers={"X-User-Role": "admin"})
    assert deleted.status_code == 200
