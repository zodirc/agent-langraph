"""Subsystem health probes for /health/ready and /health/live."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.services.db import get_postgres_pool, uses_postgres
from app.services.knowledge_store import get_knowledge_store
from app.services.llm_client import get_llm
from app.services.mcp_bridge import list_mcp_servers_health
from app.services.rate_limit import _redis_client

logger = logging.getLogger(__name__)


def check_database() -> dict[str, Any]:
    try:
        if uses_postgres():
            pool = get_postgres_pool()
            with pool.connection() as conn:
                conn.execute("SELECT 1")
            return {"status": "ok", "backend": "postgres"}
        path = Path(settings.SQLITE_PATH)
        if not path.exists():
            return {"status": "degraded", "backend": "sqlite", "detail": "db file missing"}
        conn = sqlite3.connect(str(path))
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "backend": "sqlite"}
    except Exception as exc:
        return {"status": "error", "backend": settings.STORAGE_BACKEND, "detail": str(exc)}


def check_redis() -> dict[str, Any]:
    if not settings.RATE_LIMIT_REDIS and settings.QUEUE_BACKEND != "celery":
        return {"status": "skipped", "detail": "redis not required"}
    client = _redis_client()
    if client is None:
        return {"status": "degraded", "detail": "redis unavailable"}
    try:
        client.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def check_llm() -> dict[str, Any]:
    from app.services.runtime_model_config import get_effective_model_config

    if not get_effective_model_config().enabled:
        return {"status": "skipped", "detail": "model disabled"}
    try:
        llm = get_llm()
        if llm is None:
            return {"status": "degraded", "detail": "llm not configured"}
        from app.services.runtime_model_config import get_effective_model_config

        eff = get_effective_model_config()
        return {"status": "ok", "model": eff.model_name, "provider": eff.provider}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def check_vector() -> dict[str, Any]:
    try:
        store = get_knowledge_store()
        available = bool(getattr(store._vector, "available", False))  # noqa: SLF001
        return {
            "status": "ok" if available else "degraded",
            "backend": settings.KNOWLEDGE_BACKEND,
            "available": available,
            "docs": store.count(),
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def check_mcp() -> dict[str, Any]:
    if not settings.MCP_ENABLED:
        return {"status": "skipped", "detail": "mcp disabled"}
    try:
        from app.services.mcp_manager import get_mcp_manager

        manager = get_mcp_manager()
        manager.probe_all()
        evicted = manager.evict_unhealthy() if settings.MCP_AUTO_EVICT else []
        servers = list_mcp_servers_health()
        unhealthy = [s for s in servers if not s.get("healthy")]
        snapshot = manager.status_snapshot()
        return {
            "status": "ok" if not unhealthy else "degraded",
            "servers": servers,
            "evicted": evicted,
            "manager": snapshot,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def aggregate_ready(checks: dict[str, dict[str, Any]]) -> bool:
    for name, result in checks.items():
        if name == "mcp" and result.get("status") == "skipped":
            continue
        if result.get("status") == "error":
            return False
    return checks.get("database", {}).get("status") in ("ok", "degraded")


def run_all_checks() -> dict[str, Any]:
    checks = {
        "database": check_database(),
        "redis": check_redis(),
        "llm": check_llm(),
        "vector": check_vector(),
        "mcp": check_mcp(),
    }
    return {
        "checks": checks,
        "ready": aggregate_ready(checks),
    }
