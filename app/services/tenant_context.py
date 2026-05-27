"""Multi-tenant context (schema isolation hook for Phase 8)."""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Optional

_tenant_id: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)


def set_tenant_id(tenant_id: str | None) -> None:
    if tenant_id:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", tenant_id)[:64]
        _tenant_id.set(safe or None)
    else:
        _tenant_id.set(None)


def get_tenant_id() -> Optional[str]:
    return _tenant_id.get()


def postgres_schema_for_tenant(tenant_id: str | None) -> str:
    """Return PostgreSQL schema name for tenant-isolated tables."""
    if not tenant_id:
        return "public"
    return f"tenant_{tenant_id}"


def apply_tenant_headers(headers: dict[str, str]) -> None:
    tid = headers.get("X-Tenant-Id") or headers.get("x-tenant-id")
    if tid:
        set_tenant_id(tid)
