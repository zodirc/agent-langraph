from types import SimpleNamespace

from app.config.settings import settings
from app.services.llm_client import (
    _extract_usage_from_response,
    _record_llm_usage,
    invoke_structured,
)
from app.services.tenant_quota import get_tenant_quota_store, reset_tenant_quota_store


def test_extract_usage_from_response_metadata():
    response = SimpleNamespace(
        content='{"ok": true}',
        response_metadata={
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        },
    )
    assert _extract_usage_from_response(response) == 15


def test_extract_usage_from_additional_kwargs():
    response = SimpleNamespace(
        content="hi",
        response_metadata={},
        additional_kwargs={
            "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27}
        },
    )
    assert _extract_usage_from_response(response) == 27


def test_record_llm_usage_billed_vs_logical(monkeypatch):
    tenant_id = "billing-test-tenant"
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TOKENS_PER_DAY", 0)
    before = get_tenant_quota_store().snapshot(tenant_id)["tokens_today"]
    _record_llm_usage(
        purpose="planning",
        system_prompt="a",
        user_content="b",
        response_text="c" * 400,
        billed_tokens=42,
        bill_quota=True,
        bill_cost=False,
        trace_state={"tenant_id": tenant_id},
    )
    after = get_tenant_quota_store().snapshot(tenant_id)["tokens_today"]
    assert after - before == 42


def test_invoke_structured_cache_does_not_bill_quota(monkeypatch, test_settings):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TOKENS_PER_DAY", 0)
    payload = '{"goal": "build agent", "risk_level": "LOW"}'
    invoke_structured("planning", "system", payload)
    before = get_tenant_quota_store().snapshot("default")["tokens_today"]
    invoke_structured("planning", "system", payload)
    after = get_tenant_quota_store().snapshot("default")["tokens_today"]
    assert after == before

