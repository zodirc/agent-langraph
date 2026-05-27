import pytest

from app.runtime.state import create_initial_state
from app.services.resource_budget import (
    BudgetContext,
    BudgetExceededError,
    init_task_budget,
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
