"""Context budget buckets and thresholds (optimization WP-2.1, §8.2–§8.3)."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state

# Seven budget buckets aligned with optimization.md §8.2
BUDGET_BUCKET_NAMES: tuple[str, ...] = (
    "system_policy_budget",
    "recent_messages_budget",
    "working_memory_budget",
    "retrieval_evidence_budget",
    "tool_results_budget",
    "file_slices_budget",
    "response_reserve_budget",
)

# Map internal context item buckets → budget bucket keys
_ITEM_TO_BUDGET: dict[str, str] = {
    "system_policy": "system_policy_budget",
    "current_turn": "recent_messages_budget",
    "recent_transcript": "recent_messages_budget",
    "semantic_summary": "working_memory_budget",
    "working_memory": "working_memory_budget",
    "retrieved_memory": "retrieval_evidence_budget",
    "retrieved_knowledge": "retrieval_evidence_budget",
    "tool_observations": "tool_results_budget",
    "file_context": "file_slices_budget",
    "diagnostics": "system_policy_budget",
}


def _default_bucket_caps(total: int) -> dict[str, int]:
    """Split total prompt budget across seven buckets with response reserve."""
    reserve = max(512, int(total * 0.12))
    remaining = max(total - reserve, 1024)
    weights = {
        "system_policy_budget": 0.08,
        "recent_messages_budget": 0.22,
        "working_memory_budget": 0.12,
        "retrieval_evidence_budget": 0.18,
        "tool_results_budget": 0.18,
        "file_slices_budget": 0.12,
        "response_reserve_budget": 0.10,
    }
    caps: dict[str, int] = {}
    for name in BUDGET_BUCKET_NAMES:
        if name == "response_reserve_budget":
            caps[name] = reserve
        else:
            caps[name] = max(128, int(remaining * weights.get(name, 0.1)))
    return caps


def initialize_context_budget_buckets(state: AgentState) -> dict[str, Any]:
    gov_default = int(getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 0) or 0)
    token_raw = state.get("token_budget") or {}
    total = int(token_raw.get("limit") or 0) or gov_default or 32000
    caps = _default_bucket_caps(total)
    used = {name: 0 for name in BUDGET_BUCKET_NAMES}
    soft = int(total * 0.85)
    hard = int(total * 0.95)
    emergency = total
    return {
        "total_budget": total,
        "soft_limit": soft,
        "hard_limit": hard,
        "emergency_limit": emergency,
        "caps": caps,
        "used": used,
        "compression_triggered": False,
    }


def apply_context_budget_to_state(state: AgentState) -> AgentState:
    buckets = state.get("context_budget_buckets")
    if not isinstance(buckets, dict) or not buckets.get("caps"):
        buckets = initialize_context_budget_buckets(state)
    return merge_state(state, context_budget_buckets=buckets)


def budget_bucket_for_item(item_bucket: str) -> str:
    return _ITEM_TO_BUDGET.get(item_bucket, "working_memory_budget")
