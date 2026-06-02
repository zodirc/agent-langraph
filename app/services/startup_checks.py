"""Runtime security checks executed at application startup."""

from __future__ import annotations

import logging

from app.config.settings import settings

logger = logging.getLogger(__name__)


def validate_runtime_security() -> None:
    import os

    env = (settings.APP_ENV or "").lower()
    config_path = os.environ.get("CONFIG_PATH", "").strip()
    if config_path:
        logger.info("Loading config from CONFIG_PATH=%s", config_path)

    if env not in ("production", "prod"):
        return
    if settings.AUTH_REQUIRE_IN_PRODUCTION and not settings.AUTH_ENABLED:
        hint = (
            "Set AUTH_ENABLED=true in .env / compose environment, "
            "or use docker-compose.dev.yml (APP_ENV=development)."
        )
        if "config.docker.yaml" in config_path:
            hint += " Docker production uses config/config.docker.yaml via CONFIG_PATH."
        raise RuntimeError(
            "AUTH_ENABLED=false is not allowed when APP_ENV=production. " + hint
        )
    if settings.MULTI_TENANT_ENABLED and settings.TENANT_QUOTA_BACKEND == "redis":
        if not settings.TENANT_QUOTA_REDIS:
            logger.warning(
                "tenant.quota_backend=redis but tenant.quota_use_redis is false; "
                "quota may fall back to memory"
            )
    if settings.MULTI_TENANT_ENABLED and settings.STORAGE_BACKEND == "sqlite":
        logger.warning(
            "SQLite per-tenant storage is not recommended for large-scale production; "
            "prefer PostgreSQL with schema isolation"
        )
