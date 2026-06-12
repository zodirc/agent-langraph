"""Session-scoped retrieval result cache (§7.3 scheme B)."""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from app.config.settings import settings

_CACHE: dict[tuple[str, frozenset[str], str], tuple[float, list[dict[str, Any]]]] = {}
_WS_RE = re.compile(r"\s+")


def retrieval_cache_enabled() -> bool:
    return bool(getattr(settings, "RETRIEVAL_SESSION_CACHE_ENABLED", True))


def _normalize_query(query: str) -> str:
    return _WS_RE.sub(" ", (query or "").strip().lower())


def _cache_key(query: str, domains: set[str] | frozenset[str], session_id: str) -> tuple[str, frozenset[str], str]:
    return (_normalize_query(query), frozenset(str(d).lower() for d in domains), str(session_id))


def cache_get(
    query: str,
    domains: set[str] | frozenset[str],
    session_id: str,
) -> Optional[list[dict[str, Any]]]:
    if not retrieval_cache_enabled() or not session_id:
        return None
    key = _cache_key(query, domains, session_id)
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, hits = entry
    if time.monotonic() > expires_at:
        _CACHE.pop(key, None)
        return None
    return [dict(h) for h in hits]


def cache_put(
    query: str,
    domains: set[str] | frozenset[str],
    session_id: str,
    hits: list[dict[str, Any]],
) -> None:
    if not retrieval_cache_enabled() or not session_id:
        return
    ttl = float(getattr(settings, "RETRIEVAL_SESSION_CACHE_TTL_SEC", 90.0))
    key = _cache_key(query, domains, session_id)
    _CACHE[key] = (time.monotonic() + ttl, [dict(h) for h in hits])


def invalidate_session(session_id: str) -> int:
    """Drop all cached retrieval rows for a session (e.g. after import/clear)."""
    if not session_id:
        return 0
    sid = str(session_id)
    keys = [k for k in _CACHE if k[2] == sid]
    for key in keys:
        _CACHE.pop(key, None)
    return len(keys)


def clear_cache() -> None:
    _CACHE.clear()
