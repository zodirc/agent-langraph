"""Postgres tenant schema helpers and quota backend smoke tests."""

from app.services.tenant_context import postgres_schema_for_tenant, set_tenant_id, get_tenant_id
from app.services.tenant_quota import MemoryQuotaBackend, TenantQuotaLimits


def test_postgres_schema_for_tenant():
    assert postgres_schema_for_tenant("acme") == "tenant_acme"
    assert postgres_schema_for_tenant(None) == "public"


def test_tenant_context_switch():
    set_tenant_id("a")
    assert get_tenant_id() == "a"
    set_tenant_id("b")
    assert get_tenant_id() == "b"


def test_memory_quota_backend_concurrent_limit():
    backend = MemoryQuotaBackend()
    limits = TenantQuotaLimits(max_tasks_per_day=0, max_tokens_per_day=0, max_concurrent_tasks=1)
    assert backend.check_quota("t1", "tasks", 1, limits) is True
    backend.task_started("t1")
    assert backend.check_quota("t1", "tasks", 1, limits) is False
    backend.task_finished("t1")
    assert backend.check_quota("t1", "tasks", 1, limits) is True
