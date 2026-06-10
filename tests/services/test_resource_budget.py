import pytest

from app.runtime.state import create_initial_state
from app.services.context_policy import get_prompt_context_policy
from app.services.resource_budget import (
    BudgetContext,
    BudgetExceededError,
    init_task_budget,
    resolve_prompt_token_budget,
)


def test_init_task_budget_from_payload():
    state = create_initial_state(
        input_payload={"token_budget": 5000, "cost_budget": 1.5},
    )
    updated = init_task_budget(state)
    assert updated["token_budget"]["limit"] == 5000
    assert updated["cost_budget"]["limit"] == 1.5


def test_budget_exceeded_on_before_invoke():
    ctx = BudgetContext(token_limit=50, tokens_used=45)
    with pytest.raises(BudgetExceededError):
        ctx.before_invoke("reasoning", "x" * 200, "y" * 200)


def test_resolve_prompt_token_budget_legacy_cap(monkeypatch):
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_WINDOW_ADAPTIVE_BUDGET",
        False,
    )
    state = {"token_budget": {"limit": 50000, "used": 0}}
    policy = get_prompt_context_policy("reasoning")
    budget = resolve_prompt_token_budget(state, policy_default=policy.default_token_budget)
    assert budget == policy.default_token_budget


def test_resolve_prompt_token_budget_window_adaptive(monkeypatch):
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_WINDOW_ADAPTIVE_BUDGET",
        True,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_WINDOW_UTILIZATION",
        0.6,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_PROMPT_BUDGET_FLOOR",
        12000,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_PROMPT_BUDGET_CEILING",
        160000,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.MODEL_CONTEXT_WINDOW",
        200000,
    )
    monkeypatch.setattr(
        "app.services.context_meter.resolve_model_context_window",
        lambda _s, _m=None: 200000,
    )
    state = {"token_budget": {"limit": 0, "used": 1000}}
    policy = get_prompt_context_policy("reasoning")
    budget = resolve_prompt_token_budget(
        state, policy_default=policy.default_token_budget, purpose="reasoning"
    )
    # 200k * 0.6 - 1000 used - 8192 output reserve
    assert budget > policy.default_token_budget
    assert 12000 <= budget <= 160000


def test_resolve_prompt_token_budget_respects_task_limit(monkeypatch):
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_WINDOW_ADAPTIVE_BUDGET",
        True,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_WINDOW_UTILIZATION",
        0.6,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_PROMPT_BUDGET_FLOOR",
        12000,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.CONTEXT_PROMPT_BUDGET_CEILING",
        160000,
    )
    monkeypatch.setattr(
        "app.services.resource_budget.settings.MODEL_CONTEXT_WINDOW",
        200000,
    )
    monkeypatch.setattr(
        "app.services.context_meter.resolve_model_context_window",
        lambda _s, _m=None: 200000,
    )
    state = {"token_budget": {"limit": 15000, "used": 14000}}
    policy = get_prompt_context_policy("reasoning")
    budget = resolve_prompt_token_budget(
        state, policy_default=policy.default_token_budget, purpose="reasoning"
    )
    assert budget <= 1000
    assert budget >= 512
