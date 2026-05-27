"""Health API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_live() -> None:
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_ready() -> None:
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert "checks" in resp.json()


def test_health_full() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "version" in resp.json()
