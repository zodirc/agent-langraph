import pytest

from app.config.settings import settings
from app.services.startup_checks import validate_runtime_security


def test_production_requires_auth(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_REQUIRE_IN_PRODUCTION", True)
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    with pytest.raises(RuntimeError, match="AUTH_ENABLED"):
        validate_runtime_security()


def test_development_allows_auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    validate_runtime_security()
