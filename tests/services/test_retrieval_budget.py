"""P1-2 retrieval budget linkage tests."""

import pytest

from app.services.context_policy import get_prompt_context_policy
from app.services.retrieval_budget import resolve_evidence_token_budget_for_state


def test_evidence_budget_uses_static_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_budget.settings.RETRIEVAL_BUDGET_LINK_CONTEXT",
        False,
    )
    monkeypatch.setattr(
        "app.services.retrieval_budget.settings.RETRIEVAL_EVIDENCE_TOKEN_BUDGET",
        4000,
    )
    assert resolve_evidence_token_budget_for_state({}) == 4000


def test_evidence_budget_links_to_bucket_cap(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_budget.settings.RETRIEVAL_BUDGET_LINK_CONTEXT",
        True,
    )
    monkeypatch.setattr(
        "app.services.retrieval_budget.settings.CONTEXT_WINDOW_ADAPTIVE_BUDGET",
        True,
    )
    monkeypatch.setattr(
        "app.services.retrieval_budget.settings.CONTEXT_BUCKET_CAPS_SCALE_WITH_BUDGET",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_meter.resolve_model_context_window",
        lambda _s, _m=None: 200_000,
    )
    state = {"token_budget": {"limit": 0, "used": 0}}
    budget = resolve_evidence_token_budget_for_state(state, purpose="reasoning")
    static_cap = get_prompt_context_policy("reasoning").bucket_max_tokens["retrieved_knowledge"]
    assert budget >= static_cap
