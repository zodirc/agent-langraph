"""Purpose-specific prompt context policies (ADR §5.6, §8)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings
from app.services.context_items import ContextBucketName, ContextPurpose


@dataclass(frozen=True)
class PromptContextPolicy:
    purpose: ContextPurpose
    required_buckets: frozenset[ContextBucketName]
    bucket_max_tokens: dict[ContextBucketName, int]
    compressible_buckets: frozenset[ContextBucketName]
    droppable_buckets: frozenset[ContextBucketName]
    degrade_order: tuple[ContextBucketName, ...]
    default_token_budget: int = 28000
    preserve_fidelity_buckets: frozenset[ContextBucketName] = field(
        default_factory=lambda: frozenset({"current_turn", "system_policy"})
    )
    min_bucket_tokens: dict[ContextBucketName, int] = field(default_factory=dict)
    minimum_required_items: dict[ContextBucketName, int] = field(default_factory=dict)
    allow_summary_substitute: dict[ContextBucketName, bool] = field(default_factory=dict)


def _base_budgets() -> dict[ContextBucketName, int]:
    return {
        "system_policy": 2000,
        "current_turn": 6000,
        "recent_transcript": 10000,
        "semantic_summary": 2500,
        "working_memory": 3500,
        "retrieved_memory": 2500,
        "retrieved_knowledge": 4000,
        "tool_observations": 3000,
        "file_context": 4000,
        "diagnostics": 3000,
    }


_POLICIES: dict[ContextPurpose, PromptContextPolicy] = {
    "planning": PromptContextPolicy(
        purpose="planning",
        required_buckets=frozenset(
            {
                "current_turn",
                "semantic_summary",
                "working_memory",
                "recent_transcript",
            }
        ),
        bucket_max_tokens={
            **_base_budgets(),
            "retrieved_knowledge": 1200,
            "tool_observations": 800,
            "file_context": 500,
        },
        compressible_buckets=frozenset(
            {
                "recent_transcript",
                "semantic_summary",
                "retrieved_memory",
                "retrieved_knowledge",
                "tool_observations",
            }
        ),
        droppable_buckets=frozenset(
            {
                "retrieved_knowledge",
                "tool_observations",
                "file_context",
                "diagnostics",
                "retrieved_memory",
            }
        ),
        degrade_order=(
            "retrieved_knowledge",
            "tool_observations",
            "file_context",
            "diagnostics",
            "retrieved_memory",
            "recent_transcript",
            "semantic_summary",
            "working_memory",
        ),
        default_token_budget=24000,
        min_bucket_tokens={
            "semantic_summary": 400,
            "working_memory": 600,
            "recent_transcript": 800,
        },
        minimum_required_items={"semantic_summary": 1, "working_memory": 1, "recent_transcript": 1},
        allow_summary_substitute={"semantic_summary": True, "working_memory": True},
    ),
    "reasoning": PromptContextPolicy(
        purpose="reasoning",
        required_buckets=frozenset(
            {
                "current_turn",
                "recent_transcript",
                "semantic_summary",
                "working_memory",
            }
        ),
        bucket_max_tokens=_base_budgets(),
        compressible_buckets=frozenset(
            {
                "recent_transcript",
                "semantic_summary",
                "retrieved_memory",
                "retrieved_knowledge",
                "tool_observations",
            }
        ),
        droppable_buckets=frozenset(
            {
                "file_context",
                "diagnostics",
                "retrieved_memory",
            }
        ),
        degrade_order=(
            "file_context",
            "diagnostics",
            "retrieved_memory",
            "retrieved_knowledge",
            "tool_observations",
            "recent_transcript",
            "semantic_summary",
        ),
        default_token_budget=32000,
        min_bucket_tokens={"semantic_summary": 400, "working_memory": 600, "recent_transcript": 800},
        minimum_required_items={"semantic_summary": 1, "working_memory": 1, "recent_transcript": 1},
        allow_summary_substitute={"semantic_summary": True, "working_memory": True},
    ),
    "writing": PromptContextPolicy(
        purpose="writing",
        required_buckets=frozenset(
            {
                "current_turn",
                "working_memory",
                "file_context",
                "retrieved_knowledge",
            }
        ),
        bucket_max_tokens={
            **_base_budgets(),
            "recent_transcript": 4000,
            "semantic_summary": 3000,
        },
        compressible_buckets=frozenset(
            {
                "recent_transcript",
                "semantic_summary",
                "retrieved_memory",
                "tool_observations",
            }
        ),
        droppable_buckets=frozenset(
            {
                "recent_transcript",
                "retrieved_memory",
                "tool_observations",
                "diagnostics",
            }
        ),
        degrade_order=(
            "recent_transcript",
            "tool_observations",
            "diagnostics",
            "retrieved_memory",
            "semantic_summary",
        ),
        default_token_budget=36000,
    ),
    "reviewing": PromptContextPolicy(
        purpose="reviewing",
        required_buckets=frozenset(
            {
                "current_turn",
                "working_memory",
                "file_context",
                "retrieved_knowledge",
            }
        ),
        bucket_max_tokens=_base_budgets(),
        compressible_buckets=frozenset(
            {
                "recent_transcript",
                "semantic_summary",
                "tool_observations",
            }
        ),
        droppable_buckets=frozenset(
            {
                "recent_transcript",
                "retrieved_memory",
                "tool_observations",
            }
        ),
        degrade_order=(
            "recent_transcript",
            "tool_observations",
            "retrieved_memory",
            "semantic_summary",
        ),
        default_token_budget=32000,
    ),
    "summarization": PromptContextPolicy(
        purpose="summarization",
        required_buckets=frozenset({"current_turn", "recent_transcript"}),
        bucket_max_tokens={
            "current_turn": 4000,
            "recent_transcript": 24000,
            "semantic_summary": 0,
            "working_memory": 2000,
            "retrieved_memory": 0,
            "retrieved_knowledge": 0,
            "tool_observations": 0,
            "file_context": 0,
            "diagnostics": 0,
            "system_policy": 0,
        },
        compressible_buckets=frozenset({"recent_transcript"}),
        droppable_buckets=frozenset(),
        degrade_order=("recent_transcript",),
        default_token_budget=28000,
    ),
    "reflection": PromptContextPolicy(
        purpose="reflection",
        required_buckets=frozenset(
            {"current_turn", "working_memory", "semantic_summary"}
        ),
        bucket_max_tokens={
            **_base_budgets(),
            "recent_transcript": 5000,
            "retrieved_knowledge": 1500,
            "tool_observations": 2000,
        },
        compressible_buckets=frozenset(
            {"recent_transcript", "semantic_summary", "retrieved_memory"}
        ),
        droppable_buckets=frozenset(
            {
                "retrieved_knowledge",
                "file_context",
                "diagnostics",
            }
        ),
        degrade_order=(
            "retrieved_knowledge",
            "file_context",
            "recent_transcript",
            "retrieved_memory",
        ),
        default_token_budget=20000,
    ),
    "routing": PromptContextPolicy(
        purpose="routing",
        required_buckets=frozenset({"current_turn", "working_memory"}),
        bucket_max_tokens={
            "current_turn": 4000,
            "working_memory": 2000,
            "recent_transcript": 3000,
            "semantic_summary": 1500,
            "retrieved_memory": 1000,
            "retrieved_knowledge": 500,
            "tool_observations": 0,
            "file_context": 0,
            "diagnostics": 0,
            "system_policy": 0,
        },
        compressible_buckets=frozenset(
            {"recent_transcript", "semantic_summary", "retrieved_memory"}
        ),
        droppable_buckets=frozenset(
            {
                "retrieved_knowledge",
                "tool_observations",
                "file_context",
                "diagnostics",
            }
        ),
        degrade_order=(
            "retrieved_knowledge",
            "tool_observations",
            "recent_transcript",
            "retrieved_memory",
        ),
        default_token_budget=12000,
    ),
    "intent_observation": PromptContextPolicy(
        purpose="intent_observation",
        required_buckets=frozenset(
            {"current_turn", "semantic_summary", "working_memory", "recent_transcript"}
        ),
        bucket_max_tokens={
            "current_turn": 4000,
            "working_memory": 2500,
            "recent_transcript": 3000,
            "semantic_summary": 1500,
            "retrieved_memory": 800,
            "retrieved_knowledge": 0,
            "tool_observations": 0,
            "file_context": 0,
            "diagnostics": 0,
            "system_policy": 500,
        },
        compressible_buckets=frozenset(
            {"recent_transcript", "semantic_summary", "retrieved_memory"}
        ),
        droppable_buckets=frozenset(
            {
                "retrieved_knowledge",
                "tool_observations",
                "file_context",
                "diagnostics",
            }
        ),
        degrade_order=(
            "retrieved_knowledge",
            "tool_observations",
            "recent_transcript",
            "retrieved_memory",
        ),
        default_token_budget=10000,
    ),
    "code_agent": PromptContextPolicy(
        purpose="code_agent",
        required_buckets=frozenset(
            {"current_turn", "diagnostics", "file_context", "working_memory"}
        ),
        bucket_max_tokens={
            **_base_budgets(),
            "diagnostics": 5000,
            "file_context": 6000,
            "recent_transcript": 4000,
            "retrieved_knowledge": 2000,
        },
        compressible_buckets=frozenset(
            {
                "recent_transcript",
                "semantic_summary",
                "retrieved_memory",
                "retrieved_knowledge",
                "tool_observations",
            }
        ),
        droppable_buckets=frozenset(
            {
                "retrieved_knowledge",
                "retrieved_memory",
                "semantic_summary",
                "recent_transcript",
                "tool_observations",
            }
        ),
        degrade_order=(
            "retrieved_knowledge",
            "retrieved_memory",
            "semantic_summary",
            "recent_transcript",
            "working_memory",
            "tool_observations",
            "file_context",
        ),
        default_token_budget=30000,
        preserve_fidelity_buckets=frozenset(
            {"current_turn", "diagnostics", "system_policy"}
        ),
    ),
}


def get_prompt_context_policy(purpose: str) -> PromptContextPolicy:
    if purpose == "session_turn":
        return _POLICIES["routing"]
    if purpose == "intent_observation":
        return _POLICIES["intent_observation"]
    key = purpose if purpose in _POLICIES else "reasoning"
    return _POLICIES[key]  # type: ignore[index]


def scale_policy_for_budget(
    policy: PromptContextPolicy,
    token_budget: int,
) -> PromptContextPolicy:
    """Scale per-bucket caps proportionally when window-adaptive budget is active."""
    if not getattr(settings, "CONTEXT_BUCKET_CAPS_SCALE_WITH_BUDGET", True):
        return policy
    if not getattr(settings, "CONTEXT_WINDOW_ADAPTIVE_BUDGET", False):
        return policy
    ref = policy.default_token_budget
    if ref <= 0 or token_budget <= 0:
        return policy
    ratio = token_budget / ref
    if abs(ratio - 1.0) < 0.01:
        return policy
    new_caps: dict[ContextBucketName, int] = {}
    for bucket, cap in policy.bucket_max_tokens.items():
        if cap <= 0:
            new_caps[bucket] = 0
        else:
            new_caps[bucket] = max(1, int(cap * ratio))
    return dataclasses.replace(policy, bucket_max_tokens=new_caps)


def resolve_purpose_for_state(state: dict[str, Any] | None, default: str) -> str:
    """Pick code_agent policy when workspace/code context is present."""
    if not state:
        return default
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("code_context") or payload.get("workspace_context"):
        return "code_agent"
    if (payload.get("artifact_profile") or "").lower() == "source_code":
        return "code_agent"
    return default
