"""Long-context governance eval gates (window-adaptive budget, NIH-style cases, fidelity).

Memory-safe: simulates large haystacks via ``estimated_tokens`` without
materializing multi-megabyte strings (avoids OOM on CI/dev hosts).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.context_items import ContextItem, new_context_id
from app.services.context_policy import get_prompt_context_policy, scale_policy_for_budget
from app.services.context_reducer import reduce_context_items
from app.services.context_trace import ContextAssemblyTrace
from app.services.resource_budget import resolve_prompt_token_budget

_CASES_PATH = Path(__file__).resolve().parent / "data" / "long_context_cases.json"

# Tiny filler; token budget is carried by ``estimated_tokens``, not string length.
_FILLER = "x"
_NEEDLE = "SECRET_CODE_ALPHA_7749"


def _load_cases() -> list[dict]:
    data = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return list(data.get("cases") or [])


def _adaptive_settings(monkeypatch, *, enabled: bool, window: int = 200_000) -> None:
    for mod in (
        "app.services.resource_budget",
        "app.services.context_policy",
        "app.services.prompt_context_gateway",
    ):
        monkeypatch.setattr(f"{mod}.settings.CONTEXT_WINDOW_ADAPTIVE_BUDGET", enabled)
        monkeypatch.setattr(f"{mod}.settings.CONTEXT_BUCKET_CAPS_SCALE_WITH_BUDGET", True)
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
        "app.services.context_meter.resolve_model_context_window",
        lambda _state, _mid=None, w=window: w,
    )


def _resolve_scaled_policy(
    monkeypatch,
    *,
    purpose: str,
    adaptive: bool,
    window: int = 200_000,
) -> tuple[object, int]:
    _adaptive_settings(monkeypatch, enabled=adaptive, window=window)
    state = {"token_budget": {"limit": 0, "used": 0}}
    base = get_prompt_context_policy(purpose)
    budget = resolve_prompt_token_budget(
        state,
        policy_default=base.default_token_budget,
        purpose=purpose,
    )
    return scale_policy_for_budget(base, budget), budget


def _haystack_item(case: dict) -> ContextItem:
    """Single file_context item; large token count without large content."""
    return ContextItem(
        id=new_context_id(),
        kind="file_slice",
        source="workspace",
        role="system",
        content=f"{_FILLER}\n\n{case['needle_fact']}\n\n{_FILLER}",
        priority="low",
        estimated_tokens=int(case["haystack_tokens"]),
        compressible=True,
        droppable=True,
        bucket="file_context",
    )


@pytest.mark.parametrize("case", [c for c in _load_cases() if c["family"] == "needle_in_haystack"])
def test_nih_needle_retention(case, monkeypatch):
    """Adaptive bucket caps retain needle; fixed caps drop oversized file_context."""
    item = _haystack_item(case)

    policy_off, budget_off = _resolve_scaled_policy(
        monkeypatch, purpose=case["purpose"], adaptive=False
    )
    kept_off, _, dropped_off = reduce_context_items(
        [item], policy_off, token_budget_total=budget_off
    )
    hit_off = any(case["needle_fact"] in i.content for i in kept_off)

    policy_on, budget_on = _resolve_scaled_policy(
        monkeypatch, purpose=case["purpose"], adaptive=True
    )
    kept_on, _, _ = reduce_context_items([item], policy_on, token_budget_total=budget_on)
    hit_on = any(case["needle_fact"] in i.content for i in kept_on)

    assert hit_on, f"{case['id']}: needle should be retained with adaptive budget on"
    assert dropped_off or not hit_off, (
        f"{case['id']}: fixed caps should drop haystack (hit_off={hit_off})"
    )
    assert budget_on > budget_off


def test_adaptive_budget_exceeds_policy_default(monkeypatch):
    state = {"token_budget": {"limit": 0, "used": 0}}
    _adaptive_settings(monkeypatch, enabled=True, window=200_000)
    policy = get_prompt_context_policy("reasoning")
    budget = resolve_prompt_token_budget(
        state, policy_default=policy.default_token_budget, purpose="reasoning"
    )
    assert budget > policy.default_token_budget
    assert budget <= 160_000
    assert budget >= 12_000


def test_adaptive_budget_disabled_uses_policy_cap(monkeypatch):
    state = {"token_budget": {"limit": 0, "used": 0}}
    _adaptive_settings(monkeypatch, enabled=False, window=200_000)
    policy = get_prompt_context_policy("reasoning")
    budget = resolve_prompt_token_budget(
        state, policy_default=policy.default_token_budget, purpose="reasoning"
    )
    assert budget == policy.default_token_budget


def test_scale_policy_caps_grow_with_budget(monkeypatch):
    _adaptive_settings(monkeypatch, enabled=True)
    policy = get_prompt_context_policy("reasoning")
    scaled = scale_policy_for_budget(policy, token_budget=64_000)
    assert scaled.bucket_max_tokens["file_context"] > policy.bucket_max_tokens["file_context"]
    assert scaled.bucket_max_tokens["recent_transcript"] > policy.bucket_max_tokens["recent_transcript"]


def test_multi_doc_key_points_survive_tight_budget():
    """Multi-document case: high-priority knowledge items are not all dropped."""
    case = next(c for c in _load_cases() if c["id"] == "multi_doc_aggregate")
    policy = get_prompt_context_policy(case["purpose"])
    items = [
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content=doc["key_point"],
            priority="high",
            estimated_tokens=80,
            droppable=True,
            bucket="retrieved_knowledge",
        )
        for doc in case["documents"]
    ]
    kept, _, dropped = reduce_context_items(items, policy, token_budget_total=8000)
    kept_points = {i.content for i in kept}
    coverage = len(kept_points) / len(case["documents"])
    assert coverage >= case["min_coverage"], f"coverage {coverage:.2f} below {case['min_coverage']}"
    assert not dropped or len(kept) >= 2


def test_coreference_critical_entity_not_dropped_from_trace():
    """Long-session coreference: critical entity survives compression trace."""
    case = next(c for c in _load_cases() if c["family"] == "coreference")
    entity = case["critical_entity"]
    policy = get_prompt_context_policy(case["purpose"])
    items = [
        ContextItem(
            id=new_context_id(),
            kind="recent_history",
            source="session",
            role="user",
            content=f"Discuss {entity} deployment",
            priority="medium",
            estimated_tokens=40,
            compressible=True,
            droppable=False,
            bucket="recent_transcript",
        ),
        ContextItem(
            id=new_context_id(),
            kind="working_memory",
            source="state",
            role="system",
            content=f"Plan references {entity} rollout timeline",
            priority="high",
            estimated_tokens=30,
            compressible=True,
            droppable=False,
            bucket="working_memory",
        ),
    ]
    trace = ContextAssemblyTrace(purpose=case["purpose"])
    kept, _, dropped = reduce_context_items(
        items, policy, token_budget_total=4000, trace=trace
    )
    dropped_entities = []
    for receipt in trace.to_dict().get("compression_receipts") or []:
        dropped_entities.extend(receipt.get("dropped_entities") or [])
    assert entity not in dropped_entities
    assert any(entity in (i.content or "") for i in kept)
    assert not any(
        entity in (d.content or "") for d in dropped if d.priority == "critical"
    )


def test_budget_fidelity_high_priority_evidence_preserved():
    """Budget tightening drops low-priority evidence before high-priority items."""
    case = next(c for c in _load_cases() if c["id"] == "budget_fidelity")
    policy = get_prompt_context_policy(case["purpose"])
    prefix = case["high_score_prefix"]
    per_item = int(case.get("item_tokens") or 200)
    items = [
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content=f"{prefix}_{i}: fact {i}",
            priority="high" if i < 4 else "low",
            estimated_tokens=per_item,
            droppable=True,
            bucket="retrieved_knowledge",
            meta={"relevance_score": 1.0 - i * 0.05},
        )
        for i in range(case["evidence_count"])
    ]
    kept, _, _ = reduce_context_items(items, policy, token_budget_total=2000)
    high_kept = sum(1 for i in kept if prefix in i.content and i.priority == "high")
    high_total = sum(1 for i in items if i.priority == "high")
    rate = high_kept / max(1, high_total)
    assert rate >= case["min_preservation_rate"]
