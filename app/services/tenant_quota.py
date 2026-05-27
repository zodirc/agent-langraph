"""Per-tenant resource quotas (Batch 4 — v0.13)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config.settings import settings


class TenantQuotaExceeded(Exception):
    """Raised when a tenant exceeds a configured quota."""

    def __init__(
        self,
        *,
        tenant_id: str,
        resource: str,
        limit: int | float,
        used: int | float,
    ) -> None:
        self.tenant_id = tenant_id
        self.resource = resource
        self.limit = limit
        self.used = used
        super().__init__(
            f"Tenant {tenant_id!r} exceeded {resource} quota ({used} >= {limit})"
        )

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": "quota_exceeded",
            "tenant_id": self.tenant_id,
            "resource": self.resource,
            "limit": self.limit,
            "used": self.used,
        }


@dataclass
class TenantQuotaLimits:
    max_tasks_per_day: int = 0
    max_tokens_per_day: int = 0
    max_concurrent_tasks: int = 0


@dataclass
class _TenantUsage:
    tasks_today: int = 0
    tokens_today: int = 0
    active_tasks: int = 0
    day_key: str = ""


class TenantQuotaStore:
    """In-memory quota counters (reset daily by UTC date key)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, _TenantUsage] = {}

    def _day_key(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _get(self, tenant_id: str) -> _TenantUsage:
        tid = tenant_id or "default"
        day = self._day_key()
        usage = self._usage.get(tid)
        if usage is None or usage.day_key != day:
            usage = _TenantUsage(day_key=day)
            self._usage[tid] = usage
        return usage

    def limits(self) -> TenantQuotaLimits:
        return TenantQuotaLimits(
            max_tasks_per_day=int(getattr(settings, "TENANT_MAX_TASKS_PER_DAY", 0)),
            max_tokens_per_day=int(getattr(settings, "TENANT_MAX_TOKENS_PER_DAY", 0)),
            max_concurrent_tasks=int(getattr(settings, "TENANT_MAX_CONCURRENT_TASKS", 0)),
        )

    def check_quota(self, tenant_id: str, resource: str, amount: int = 1) -> bool:
        if not getattr(settings, "MULTI_TENANT_ENABLED", False):
            return True
        lim = self.limits()
        with self._lock:
            usage = self._get(tenant_id or "default")
            if resource == "tasks":
                if lim.max_tasks_per_day > 0 and usage.tasks_today + amount > lim.max_tasks_per_day:
                    return False
                if lim.max_concurrent_tasks > 0 and usage.active_tasks + amount > lim.max_concurrent_tasks:
                    return False
                return True
            if resource == "tokens":
                if lim.max_tokens_per_day > 0 and usage.tokens_today + amount > lim.max_tokens_per_day:
                    return False
                return True
        return True

    def record_usage(self, tenant_id: str, resource: str, amount: int) -> None:
        if amount <= 0:
            return
        with self._lock:
            usage = self._get(tenant_id or "default")
            if resource == "tasks":
                usage.tasks_today += amount
            elif resource == "tokens":
                usage.tokens_today += amount

    def task_started(self, tenant_id: str) -> None:
        with self._lock:
            usage = self._get(tenant_id or "default")
            usage.active_tasks += 1
            usage.tasks_today += 1

    def task_finished(self, tenant_id: str) -> None:
        with self._lock:
            usage = self._get(tenant_id or "default")
            usage.active_tasks = max(0, usage.active_tasks - 1)

    def snapshot(self, tenant_id: str) -> dict[str, int]:
        with self._lock:
            usage = self._get(tenant_id or "default")
            return {
                "tasks_today": usage.tasks_today,
                "tokens_today": usage.tokens_today,
                "active_tasks": usage.active_tasks,
            }

    def quota_report(self, tenant_id: str) -> dict[str, Any]:
        """Human-readable quota status for dashboards and admin API."""
        tid = tenant_id or "default"
        lim = self.limits()
        usage = self.snapshot(tid)

        def _remaining(limit: int, used: int) -> int | None:
            if limit <= 0:
                return None
            return max(0, limit - used)

        return {
            "tenant_id": tid,
            "multi_tenant_enabled": bool(getattr(settings, "MULTI_TENANT_ENABLED", False)),
            "limits": {
                "max_tasks_per_day": lim.max_tasks_per_day,
                "max_tokens_per_day": lim.max_tokens_per_day,
                "max_concurrent_tasks": lim.max_concurrent_tasks,
            },
            "usage": usage,
            "remaining": {
                "tasks_per_day": _remaining(lim.max_tasks_per_day, usage["tasks_today"]),
                "tokens_per_day": _remaining(lim.max_tokens_per_day, usage["tokens_today"]),
                "concurrent_tasks": _remaining(
                    lim.max_concurrent_tasks, usage["active_tasks"]
                ),
            },
            "within_quota": {
                "tasks": self.check_quota(tid, "tasks", 1),
                "tokens": self.check_quota(tid, "tokens", 1),
            },
        }


_store: TenantQuotaStore | None = None


def get_tenant_quota_store() -> TenantQuotaStore:
    global _store
    if _store is None:
        _store = TenantQuotaStore()
    return _store


def reset_tenant_quota_store() -> None:
    """Test helper."""
    global _store
    _store = TenantQuotaStore()


def check_quota(tenant_id: str, resource: str, amount: int = 1) -> bool:
    return get_tenant_quota_store().check_quota(tenant_id, resource, amount)


def record_usage(tenant_id: str, resource: str, amount: int) -> None:
    get_tenant_quota_store().record_usage(tenant_id, resource, amount)


def quota_report(tenant_id: str) -> dict[str, Any]:
    return get_tenant_quota_store().quota_report(tenant_id)


def require_quota(tenant_id: str, resource: str, amount: int = 1) -> None:
    store = get_tenant_quota_store()
    if store.check_quota(tenant_id, resource, amount):
        return
    lim = store.limits()
    snap = store.snapshot(tenant_id or "default")
    if resource == "tasks":
        limit = lim.max_concurrent_tasks or lim.max_tasks_per_day
        used = snap["active_tasks"] if lim.max_concurrent_tasks else snap["tasks_today"]
    else:
        limit = lim.max_tokens_per_day
        used = snap["tokens_today"]
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_tenant_quota_exceeded(tenant_id or "default", resource)
    raise TenantQuotaExceeded(
        tenant_id=tenant_id or "default",
        resource=resource,
        limit=limit,
        used=used,
    )
