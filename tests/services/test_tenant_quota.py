from app.services.tenant_quota import (
    check_quota,
    record_usage,
    require_quota,
    reset_tenant_quota_store,
    TenantQuotaExceeded,
)
from app.config.settings import settings


def test_quota_not_exceeded(monkeypatch):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TASKS_PER_DAY", 100)
    assert check_quota("tenant_a", "tasks", 1) is True


def test_quota_exceeded(monkeypatch):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TASKS_PER_DAY", 3)
    monkeypatch.setattr(settings, "TENANT_MAX_CONCURRENT_TASKS", 0)
    for _ in range(3):
        record_usage("tenant_b", "tasks", 1)
    assert check_quota("tenant_b", "tasks", 1) is False


def test_tenant_quota_isolation(monkeypatch):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TASKS_PER_DAY", 50)
    record_usage("tenant_c", "tasks", 50)
    assert check_quota("tenant_d", "tasks", 1) is True


def test_require_quota_raises(monkeypatch):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TOKENS_PER_DAY", 10)
    record_usage("tenant_e", "tokens", 10)
    try:
        require_quota("tenant_e", "tokens", 1)
        assert False, "expected TenantQuotaExceeded"
    except TenantQuotaExceeded as exc:
        assert exc.resource == "tokens"
        assert exc.to_detail()["error"] == "quota_exceeded"
