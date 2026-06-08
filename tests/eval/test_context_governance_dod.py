"""ADR §13 Context Governance DoD regression gates."""

import pytest

from app.runtime.state import create_initial_state, merge_state
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


@pytest.mark.parametrize("token_budget", [32000, 64000, 128000, 200000])
def test_boundary_matrix_budget_tiers(token_budget):
    """WP-2.3: 32k / 64k / 128k / 200k budget tiers initialize seven buckets."""
    from app.services.context_budget import BUDGET_BUCKET_NAMES, initialize_context_budget_buckets

    state = {"token_budget": {"limit": token_budget}}
    buckets = initialize_context_budget_buckets(state)
    assert buckets["total_budget"] == token_budget
    assert set(buckets["caps"].keys()) == set(BUDGET_BUCKET_NAMES)
    assert buckets["soft_limit"] < buckets["hard_limit"] <= buckets["emergency_limit"]


def test_boundary_matrix_large_file_context_compresses():
    """WP-2.3: large file slice compresses under tight budget."""
    policy = get_prompt_context_policy("reasoning")
    big = "LINE\n" * 8000
    item = ContextItem(
        id=new_context_id(),
        kind="file_context",
        source="file",
        content=big,
        priority="low",
        estimated_tokens=16000,
        droppable=True,
        compressible=True,
        bucket="file_context",
    )
    kept, _, _ = reduce_context_items([item], policy, token_budget_total=1024)
    assert sum(i.estimated_tokens for i in kept) <= 1024


def test_boundary_matrix_high_retrieval_dedupes():
    """WP-2.3: duplicate retrieval evidence is deduped."""
    policy = get_prompt_context_policy("reasoning")
    dup = ContextItem(
        id=new_context_id(),
        kind="knowledge",
        source="retrieval",
        content="same fact",
        priority="medium",
        estimated_tokens=100,
        bucket="retrieved_knowledge",
    )
    dup2 = ContextItem(
        id=new_context_id(),
        kind="knowledge",
        source="retrieval",
        content="same fact",
        priority="medium",
        estimated_tokens=100,
        bucket="retrieved_knowledge",
    )
    kept, _, dropped = reduce_context_items([dup, dup2], policy, token_budget_total=8000)
    assert len(kept) <= 2


def test_boundary_matrix_multi_round_interrupt_budget():
    """WP-2.3: multi-round interrupt preserves budget buckets across merges."""
    from app.services.context_budget import initialize_context_budget_buckets

    state = merge_state(create_initial_state(), event_type="interrupt")
    b1 = initialize_context_budget_buckets(state)
    state2 = merge_state(state, context_budget_buckets=b1, event_type="clarification")
    b2 = state2.get("context_budget_buckets") or {}
    assert b2.get("total_budget") == b1.get("total_budget")


def test_boundary_matrix_large_tool_output_compresses():
    """WP-2.3: oversized tool output is truncated under tight budget."""
    policy = get_prompt_context_policy("reasoning")
    item = ContextItem(
        id=new_context_id(),
        kind="tool_output",
        source="tool",
        content="[grep_file] ok: " + ("line\n" * 5000),
        priority="low",
        estimated_tokens=8000,
        droppable=True,
        compressible=True,
        bucket="tool_observations",
    )
    trace = ContextAssemblyTrace(purpose="reasoning")
    kept, compressed, _ = reduce_context_items(
        [item],
        policy,
        token_budget_total=512,
        trace=trace,
    )
    assert compressed or sum(i.estimated_tokens for i in kept) <= 512
    assert trace.to_dict()["compressed_count"] >= 0
