"""API rate limiting — in-memory sliding window with optional Redis backend."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request

import app.config.settings as settings_module

# Backward-compatible alias for tests/older imports.
settings = settings_module.settings

logger = logging.getLogger(__name__)

from app.services.redis_client import get_redis_client


def _redis_client() -> object | None:
    if not settings.RATE_LIMIT_REDIS:
        return None
    return get_redis_client()


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str, window_sec: float, now: float) -> None:
        cutoff = now - window_sec
        self._windows[key] = [t for t in self._windows[key] if t > cutoff]

    def _memory_check(self, key: str, limit: int, window_sec: float) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune(key, window_sec, now)
            if len(self._windows[key]) >= limit:
                return False
            self._windows[key].append(now)
            return True

    def _redis_check(self, key: str, limit: int, window_sec: int) -> bool:
        client = _redis_client()
        if client is None:
            return self._memory_check(key, limit, float(window_sec))
        rkey = f"agent:ratelimit:{key}"
        try:
            pipe = client.pipeline()
            pipe.incr(rkey)
            pipe.expire(rkey, window_sec)
            count, _ = pipe.execute()
            return int(count) <= limit
        except Exception as exc:
            logger.warning("Redis rate limit failed, memory fallback: %s", exc)
            return self._memory_check(key, limit, float(window_sec))

    def check(self, key: str, limit: int, window_sec: int = 60) -> bool:
        if limit <= 0:
            return True
        if settings.RATE_LIMIT_REDIS and _redis_client() is not None:
            return self._redis_check(key, limit, window_sec)
        return self._memory_check(key, limit, float(window_sec))


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def rate_limit_key_user(request: Request, user_id: str) -> str:
    return f"user:{user_id}"


def rate_limit_key_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    if request.client:
        return f"ip:{request.client.host}"
    return "ip:unknown"


def enforce_rate_limits(request: Request, user_id: str) -> None:
    # Read the latest settings (tests may monkeypatch app.config.settings.settings).
    if not settings_module.settings.RATE_LIMIT_ENABLED:
        return
    limiter = get_rate_limiter()
    window = settings_module.settings.RATE_LIMIT_WINDOW_SEC
    user_key = rate_limit_key_user(request, user_id)
    ip_key = rate_limit_key_ip(request)

    if not limiter.check(user_key, settings_module.settings.RATE_LIMIT_USER_PER_MIN, window):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings_module.settings.RATE_LIMIT_USER_PER_MIN} requests per {window}s (user)",
        )
    if not limiter.check(ip_key, settings_module.settings.RATE_LIMIT_IP_PER_MIN, window):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings_module.settings.RATE_LIMIT_IP_PER_MIN} requests per {window}s (IP)",
        )
