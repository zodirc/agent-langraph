from app.runtime.state import create_initial_state, merge_state
from app.services.context_meter import build_session_meter, read_session_token_totals
from app.services.resource_budget import (
    BudgetContext,
    record_session_token_usage,
)
from app.services.token_counter import count_llm_exchange_tokens


def test_build_session_meter_empty():
    state = create_initial_state(session_id="sess-empty")
    meter = build_session_meter(state)
    assert meter["session_tokens_consumed"] is None
    assert meter["context_length_used_tokens"] is None
    assert meter["billing_source"] == "none"


def test_count_llm_exchange_tokens_positive():
    detail = count_llm_exchange_tokens(
        model_name="gpt-4",
        system_prompt="system",
        user_content="user message",
        response_text="assistant reply",
    )
    assert detail["prompt_tokens"] > 0
    assert detail["completion_tokens"] > 0
    assert detail["total_tokens"] == detail["prompt_tokens"] + detail["completion_tokens"]


def test_record_session_token_usage_stores_provider_detail():
    state = create_initial_state(session_id="sess-usage")
    state = merge_state(state, token_budget={"limit": 0, "used": 0})
    record_session_token_usage(
        state,
        system_prompt="sys",
        user_content="user",
        response_text="out",
        billed_tokens=150,
        usage_detail={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        purpose="reasoning",
    )
    meter = build_session_meter(state)
    assert meter["session_tokens_consumed"] == 150
    assert meter["billing_source"] == "provider"
    assert meter["last_prompt_tokens"] == 100
    assert meter["last_request_tokens"] == 150


def test_record_session_token_usage_local_fallback_without_provider():
    state = create_initial_state(session_id="sess-local")
    state = merge_state(state, token_budget={"limit": 0, "used": 0})
    record_session_token_usage(
        state,
        system_prompt="sys" * 50,
        user_content="user" * 80,
        response_text="out" * 40,
        billed_tokens=None,
        usage_detail=None,
        purpose="reasoning",
    )
    meter = build_session_meter(state)
    assert meter["session_tokens_consumed"] > 0
    assert meter["billing_source"] == "local"
    assert meter["context_length_used_tokens"] > 0
    assert meter["last_request_tokens"] > 0


def test_record_session_token_usage_with_budget_ctx_no_double_count():
    state = create_initial_state(session_id="sess-bctx")
    state = merge_state(state, token_budget={"limit": 0, "used": 0, "used_billed": 0})
    ctx = BudgetContext()
    ctx.after_invoke(
        "reasoning",
        "sys",
        "user",
        "out",
        billed_tokens=40,
    )
    record_session_token_usage(
        state,
        system_prompt="sys",
        user_content="user",
        response_text="out",
        billed_tokens=40,
        usage_detail={
            "prompt_tokens": 30,
            "completion_tokens": 10,
            "total_tokens": 40,
        },
        purpose="reasoning",
        budget_ctx=ctx,
    )
    assert int(state["token_budget"]["used_billed"]) == 40
    assert state["token_budget"]["last_usage"]["prompt_tokens"] == 30
    assert int(state["token_budget"]["llm_call_count"]) == 1


def test_apply_to_state_preserves_last_usage_and_local():
    state = create_initial_state(session_id="sess-preserve")
    state = merge_state(
        state,
        token_budget={
            "limit": 1000,
            "used": 10,
            "used_billed": 10,
            "used_local": 12,
            "last_usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            "last_usage_local": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            "llm_call_count": 1,
        },
    )
    ctx = BudgetContext.from_state(state)
    ctx.after_invoke("reasoning", "a", "b", "c", billed_tokens=5)
    updated = ctx.apply_to_state(state)
    assert updated["token_budget"]["last_usage"]["prompt_tokens"] == 8
    assert updated["token_budget"]["last_usage_local"]["prompt_tokens"] == 9
    assert updated["token_budget"]["llm_call_count"] == 1
    assert updated["token_budget"]["used_billed"] == 15


def test_read_session_token_totals_provider_preferred():
    state = {
        "token_budget": {
            "used_billed": 900,
            "used_local": 5000,
            "last_usage": {"prompt_tokens": 321, "completion_tokens": 10, "total_tokens": 331},
            "last_usage_local": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }
    }
    totals = read_session_token_totals(state)
    assert totals["session_tokens_consumed_billed"] == 900
    assert totals["last_prompt_tokens"] == 321
    assert totals["billing_source"] == "provider"
