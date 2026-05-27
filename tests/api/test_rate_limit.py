"""API rate limiting tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.api.deps import get_current_principal
from app.config.settings import Settings
from app.services.auth_service import AuthPrincipal
from app.services.rate_limit import enforce_rate_limits, get_rate_limiter


@pytest.fixture
def rate_limit_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    test_settings.AUTH_ENABLED = False
    test_settings.RATE_LIMIT_ENABLED = True
    test_settings.RATE_LIMIT_USER_PER_MIN = 2
    test_settings.RATE_LIMIT_IP_PER_MIN = 100
    test_settings.RATE_LIMIT_WINDOW_SEC = 60
    monkeypatch.setattr("app.config.settings.settings", test_settings)
    monkeypatch.setattr("app.services.rate_limit.settings", test_settings)
    monkeypatch.setattr("app.api.deps.settings", test_settings)
    get_rate_limiter()._windows.clear()  # noqa: SLF001
    return test_settings


@pytest.fixture
def limit_client(rate_limit_settings: Settings) -> TestClient:
    app = FastAPI()

    @app.post("/probe")
    def probe(
        principal: AuthPrincipal = Depends(get_current_principal),
    ) -> dict[str, str]:
        return {"user_id": principal.user_id}

    return TestClient(app)


def test_rate_limit_service_blocks_third_call(rate_limit_settings: Settings) -> None:
    from starlette.requests import Request

    scope = {"type": "http", "headers": [], "client": ("127.0.0.1", 1234)}
    request = Request(scope)
    enforce_rate_limits(request, "user-a")
    enforce_rate_limits(request, "user-a")
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limits(request, "user-a")
    assert exc.value.status_code == 429


def test_rate_limit_returns_429(limit_client: TestClient) -> None:
    headers = {"X-User-Id": "rate-test-user"}
    for _ in range(2):
        resp = limit_client.post("/probe", headers=headers)
        assert resp.status_code == 200
    resp = limit_client.post("/probe", headers=headers)
    assert resp.status_code == 429
