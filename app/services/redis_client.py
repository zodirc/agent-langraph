"""Shared optional Redis client for rate limits and tenant quotas."""

from __future__ import annotations

import logging

import app.config.settings as settings_module

logger = logging.getLogger(__name__)

_REDIS_CLIENT: object | None = None
_REDIS_TRIED = False


def get_redis_client(*, required: bool = False) -> object | None:
    """Return a Redis client when redis URL is configured and reachable."""
    global _REDIS_CLIENT, _REDIS_TRIED
    if _REDIS_TRIED:
        if required and _REDIS_CLIENT is None:
            return None
        return _REDIS_CLIENT
    _REDIS_TRIED = True
    settings = settings_module.settings
    use_redis = bool(
        getattr(settings, "RATE_LIMIT_REDIS", False)
        or getattr(settings, "TENANT_QUOTA_REDIS", False)
    )
    if not use_redis:
        return None
    try:
        import redis

        _REDIS_CLIENT = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _REDIS_CLIENT.ping()
        logger.info("Redis client connected at %s", settings.REDIS_URL)
    except Exception as exc:
        logger.warning("Redis unavailable: %s", exc)
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def reset_redis_client_for_tests() -> None:
    """Test helper — force reconnect on next use."""
    global _REDIS_CLIENT, _REDIS_TRIED
    _REDIS_CLIENT = None
    _REDIS_TRIED = False
