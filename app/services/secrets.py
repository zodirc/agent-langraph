"""密钥提供者抽象：环境变量、k8s、vault 等后端。

Secrets provider abstraction with env, k8s, and vault backends.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROVIDER: Optional["SecretsProvider"] = None


class SecretsProvider(ABC):
    @abstractmethod
    def get(self, key: str, *, default: str = "") -> str:
        raise NotImplementedError


class EnvSecretsProvider(SecretsProvider):
    """Load secrets from environment variables (12-factor dev default)."""

    def get(self, key: str, *, default: str = "") -> str:
        return os.environ.get(key, default).strip()


class K8sSecretsProvider(SecretsProvider):
    """Read secrets from mounted files at /var/run/secrets/agent/."""

    def __init__(self, base_path: str = "/var/run/secrets/agent") -> None:
        self._base = Path(base_path)

    def get(self, key: str, *, default: str = "") -> str:
        path = self._base / key
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return os.environ.get(key, default).strip()


class VaultSecretsProvider(SecretsProvider):
    """
    Vault KV v2 stub — falls back to env when VAULT_ADDR unset or httpx unavailable.
    Set VAULT_ADDR + VAULT_TOKEN + VAULT_SECRET_PATH for production.
    """

    def __init__(self) -> None:
        self._addr = os.environ.get("VAULT_ADDR", "").strip().rstrip("/")
        self._token = os.environ.get("VAULT_TOKEN", "").strip()
        self._path = os.environ.get("VAULT_SECRET_PATH", "secret/data/agent-langraph").strip()
        self._fallback = EnvSecretsProvider()

    def get(self, key: str, *, default: str = "") -> str:
        if not self._addr or not self._token:
            return self._fallback.get(key, default=default)
        try:
            import httpx

            url = f"{self._addr}/v1/{self._path}"
            resp = httpx.get(
                url,
                headers={"X-Vault-Token": self._token},
                timeout=5.0,
            )
            if resp.status_code != 200:
                logger.warning("Vault read failed (%s), fallback to env for %s", resp.status_code, key)
                return self._fallback.get(key, default=default)
            data = resp.json().get("data", {}).get("data", {})
            value = data.get(key)
            if value is not None:
                return str(value).strip()
        except Exception as exc:
            logger.warning("Vault unavailable (%s), env fallback for %s", exc, key)
        return self._fallback.get(key, default=default)


def create_secrets_provider(backend: str | None = None) -> SecretsProvider:
    backend = (backend or os.environ.get("SECRETS_BACKEND", "env")).lower().strip()
    if backend in ("k8s", "kubernetes"):
        return K8sSecretsProvider(
            base_path=os.environ.get("K8S_SECRETS_PATH", "/var/run/secrets/agent")
        )
    if backend == "vault":
        return VaultSecretsProvider()
    return EnvSecretsProvider()


def secrets_provider() -> SecretsProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = create_secrets_provider()
    return _PROVIDER


def reset_secrets_provider() -> None:
    global _PROVIDER
    _PROVIDER = None
