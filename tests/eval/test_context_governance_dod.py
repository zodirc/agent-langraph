"""ADR §13 Context Governance DoD regression gates."""

from app.services.context_items import ContextItem, new_context_id
from app.services.context_policy import get_prompt_context_policy
from app.services.context_reducer import reduce_context_items
from app.services.context_registry import (
    items_from_knowledge,
    merge_registry_items,
    persist_retrieval_context,
)
from app.services.context_trace import ContextAssemblyTrace
from app.services.prompt_context_gateway import (
    build_context_envelope,
    context_governance_enabled,
    prepare_governed_payload,
)
from app.services.resource_budget import resolve_prompt_token_budget


def test_dod_five_context_kinds_in_collect(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    state = {
        "task_id": "dod-1",
        "session_id": "dod-1",
        "input_payload": {"goal": "test"},
        "conversation_history": [{"role": "user", "content": "hi", "at": "t"}],
        "retrieved_knowledge": [{"content": "doc fact", "doc_id": "d1"}],
        "memory_hits": [{"summary": "past episode"}],
        "turn_facts": {"tools_executed": [{"tool": "echo", "status": "ok"}]},
        "plan": ["step"],
    }
    env = build_context_envelope(state, purpose="reasoning", token_budget_total=12000)
    kinds = {i.kind for i in env.items_kept}
    assert "user_turn" in kinds or "recent_history" in kinds
    assert "working_memory" in kinds
    assert "knowledge" in kinds or any(
        i.resolve_bucket() == "retrieved_knowledge" for i in env.items_kept
    )


def test_dod_registry_persist_and_recall():
    state = {
        "task_id": "dod-2",
        "session_id": "dod-2",
        "input_payload": {},
    }
    items = items_from_knowledge([{"content": "stored knowledge", "id": "k1"}])
    updated = persist_retrieval_context(state, knowledge=[{"content": "stored knowledge"}], memories=[])
    assert updated.get("context_item_registry")
    env = build_context_envelope(updated, purpose="reasoning", token_budget_total=8000)
    assert env.items_kept


def test_dod_token_budget_unified_with_task_budget():
    state = {
        "task_id": "dod-3",
        "session_id": "dod-3",
        "token_budget": {"limit": 5000, "used": 4000},
    }
    policy = get_prompt_context_policy("planning")
    remaining = resolve_prompt_token_budget(state, policy_default=policy.default_token_budget)
    assert remaining <= 1000
    assert remaining >= 512


def test_dod_deterministic_degrade_drops_knowledge_first():
    policy = get_prompt_context_policy("planning")
    items = [
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content="k" * 5000,
            priority="low",
            estimated_tokens=2000,
            droppable=True,
            bucket="retrieved_knowledge",
        ),
        ContextItem(
            id=new_context_id(),
            kind="user_turn",
            source="session",
            content="must keep",
            priority="critical",
            estimated_tokens=40,
            droppable=False,
            bucket="current_turn",
        ),
    ]
    trace = ContextAssemblyTrace(purpose="planning")
    kept, _, dropped = reduce_context_items(
        items, policy, token_budget_total=80, trace=trace
    )
    assert any(i.kind == "user_turn" for i in kept)
    assert dropped


def test_dod_prepare_governed_replaces_history(monkeypatch):
    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.CONTEXT_GOVERNANCE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    raw_len = 50
    state = {
        "task_id": "dod-4",
        "session_id": "dod-4",
        "input_payload": {"goal": "current"},
        "conversation_history": [{"role": "user", "content": "x" * raw_len}],
    }
    out, _ = prepare_governed_payload(
        state,
        "planning",
        {"goal": "current", "conversation_history": [{"role": "user", "content": "x" * raw_len}]},
    )
    assert "context_governance" in out
    assert out["conversation_history"] is not None


def test_dod_all_purposes_have_policy():
    for purpose in (
        "planning",
        "reasoning",
        "writing",
        "reviewing",
        "reflection",
        "routing",
        "summarization",
        "code_agent",
    ):
        policy = get_prompt_context_policy(purpose)
        assert policy.default_token_budget > 0
        assert policy.degrade_order


def test_context_governance_enabled_default():
    assert context_governance_enabled() is True
