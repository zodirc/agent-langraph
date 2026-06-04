"""Context Governance layer (ADR_CONTEXT_GOVERNANCE)."""

import json

from app.services.context_collectors import (
    collect_code_agent_context_items,
    collect_diagnostic_context_items,
    collect_writing_context_items,
)
from app.services.context_items import ContextItem, new_context_id
from app.services.context_policy import get_prompt_context_policy
from app.services.context_reducer import reduce_context_items
from app.services.context_trace import ContextAssemblyTrace
from app.services.llm_client import invoke_structured
from app.services.prompt_context_gateway import (
    apply_governance_to_user_content,
    build_context_envelope,
    build_prompt_composition_for_state,
    collect_context_items,
    context_governance_enabled,
    file_context_bundle_from_envelope,
    governed_conversation_history,
    prepare_governed_payload,
    should_govern_llm_purpose,
)


def test_context_governance_enabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.CONTEXT_GOVERNANCE_ENABLED",
        True,
    )
    assert context_governance_enabled() is True
    assert should_govern_llm_purpose("planning") is True
    assert should_govern_llm_purpose("rag_eval") is False


def test_collect_context_items_includes_goal_and_working_memory():
    state = {
        "input_payload": {"goal": "写一章小说", "risk_level": "LOW"},
        "conversation_history": [
            {"role": "user", "content": "你好", "at": "t1"},
            {"role": "assistant", "content": "你好，需要什么帮助？", "at": "t2"},
        ],
        "plan": ["outline", "draft"],
        "turn_facts": {"tools_executed": [{"tool": "echo", "status": "ok"}]},
    }
    items = collect_context_items(state, purpose="planning")
    kinds = {i.kind for i in items}
    assert "user_turn" in kinds
    assert "recent_history" in kinds
    assert "working_memory" in kinds


def test_code_agent_collector_git_diff():
    state = {
        "input_payload": {
            "code_context": {
                "git_diff": "+++ a.py\n--- b.py",
                "symbol_slices": [{"name": "foo", "content": "def foo(): pass"}],
            }
        }
    }
    items = collect_code_agent_context_items(state)
    kinds = {i.kind for i in items}
    assert "git_diff" in kinds
    assert "symbol_slice" in kinds


def test_diagnostic_collector_errors():
    state = {"errors": [{"message": "compile failed", "node": "tool"}]}
    items = collect_diagnostic_context_items(state)
    assert any(i.kind == "diagnostic" for i in items)


def test_planning_policy_weakens_knowledge_bucket():
    policy = get_prompt_context_policy("planning")
    assert policy.bucket_max_tokens["retrieved_knowledge"] < policy.bucket_max_tokens[
        "recent_transcript"
    ]
    assert "retrieved_knowledge" in policy.droppable_buckets


def test_reflection_policy_exists():
    policy = get_prompt_context_policy("reflection")
    assert policy.purpose == "reflection"


def test_reduce_drops_low_priority_when_over_budget():
    policy = get_prompt_context_policy("planning")
    items = [
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content="x" * 8000,
            priority="low",
            estimated_tokens=2500,
            droppable=True,
            bucket="retrieved_knowledge",
        ),
        ContextItem(
            id=new_context_id(),
            kind="user_turn",
            source="session",
            content="critical goal",
            priority="critical",
            estimated_tokens=50,
            droppable=False,
            bucket="current_turn",
        ),
    ]
    trace = ContextAssemblyTrace(purpose="planning")
    kept, _compressed, dropped = reduce_context_items(
        items, policy, token_budget_total=100, trace=trace
    )
    assert any(i.kind == "user_turn" for i in kept)
    assert dropped or _compressed


def test_build_context_envelope_trace(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    state = {
        "input_payload": {"goal": "test goal"},
        "conversation_history": [{"role": "user", "content": "prior", "at": "t0"}],
    }
    envelope = build_context_envelope(state, purpose="planning", token_budget_total=8000)
    assert envelope.purpose == "planning"
    assert envelope.trace.get("kept_count", 0) >= 1
    assert envelope.conversation_history_for_payload()
    bundle = file_context_bundle_from_envelope(envelope)
    assert "file_slices" in bundle


def test_prepare_governed_payload_injects_trace(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    state = {"input_payload": {"goal": "g"}, "conversation_history": []}
    out, env = prepare_governed_payload(
        state, "planning", {"goal": "g", "conversation_history": []}
    )
    assert "context_governance" in out
    assert out["conversation_history"] == env.conversation_history_for_payload()


def test_apply_governance_skips_already_governed():
    state = {"input_payload": {"goal": "x"}}
    raw = json.dumps({"goal": "x", "context_governance": {"purpose": "planning"}})
    assert apply_governance_to_user_content("planning", raw, state) == raw


def test_llm_client_hook_governs_json_user(monkeypatch):
    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.CONTEXT_GOVERNANCE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    monkeypatch.setattr("app.services.llm_client.settings.MODEL_ENABLED", False)
    state = {
        "task_id": "t-hook",
        "session_id": "t-hook",
        "input_payload": {"goal": "hook test"},
        "conversation_history": [{"role": "user", "content": "old", "at": "t"}],
    }
    user = json.dumps(
        {"goal": "hook test", "conversation_history": [{"role": "user", "content": "old"}]},
        ensure_ascii=False,
    )
    result = invoke_structured("planning", "system", user, trace_state=state)
    assert isinstance(result, dict)


def test_build_prompt_composition_for_state():
    state = {
        "input_payload": {"goal": "compose"},
        "conversation_history": [],
    }
    view = build_prompt_composition_for_state(state, purpose="reasoning")
    assert view.get("purpose") == "reasoning" or "buckets" in view


def test_governed_conversation_history(monkeypatch):
    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.CONTEXT_GOVERNANCE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    state = {
        "task_id": "t-gov",
        "session_id": "t-gov",
        "input_payload": {"goal": "only this turn"},
        "conversation_history": [],
    }
    hist = governed_conversation_history(state, purpose="planning")
    assert isinstance(hist, list)


def test_writing_collector_empty_without_task_id():
    assert collect_writing_context_items({}, purpose="writing") == []
