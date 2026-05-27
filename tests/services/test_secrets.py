"""Secrets provider tests."""

from __future__ import annotations

import os

import pytest

from app.services.secrets import (
    EnvSecretsProvider,
    K8sSecretsProvider,
    create_secrets_provider,
    reset_secrets_provider,
)


def test_env_secrets_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SECRET_KEY", "secret-value")
    provider = EnvSecretsProvider()
    assert provider.get("TEST_SECRET_KEY") == "secret-value"
    assert provider.get("MISSING", default="fallback") == "fallback"


def test_k8s_secrets_fallback_to_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FROM_ENV", "env-val")
    provider = K8sSecretsProvider(base_path=str(tmp_path))
    assert provider.get("FROM_ENV") == "env-val"


def test_create_secrets_provider_env() -> None:
    reset_secrets_provider()
    provider = create_secrets_provider("env")
    assert provider.get("NONEXISTENT", default="x") == "x"
