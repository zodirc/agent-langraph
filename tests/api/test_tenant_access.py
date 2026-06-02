import jwt
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app


def _token(*, tenant_id: str = "acme", role: str = "user", sub: str = "u1") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "tenant_id": tenant_id,
            "exp": 9_999_999_999,
        },
        settings.APP_SECRET_KEY,
        algorithm="HS256",
    )


def test_tenant_header_mismatch_returns_403(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    client = TestClient(app)
    token = _token(tenant_id="acme")
    response = client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "other"},
    )
    assert response.status_code == 403


def test_matching_tenant_header_allowed(monkeypatch, test_settings):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    client = TestClient(app)
    token = _token(tenant_id="acme")
    response = client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "acme"},
    )
    assert response.status_code == 200
