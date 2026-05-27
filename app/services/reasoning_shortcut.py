"""
When to reuse an execution summary vs run full reasoning (multi-turn safe).
"""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, TaskStatus

# Payload keys that must not carry over to a new user turn.
_TURN_CARRYOVER_POP = (
    "skip_reasoning_after_tools",
    "route_audit_replan_feedback",
)

_KINDS_NO_EXECUTION_SUMMARY = frozenset({"qa", "retry_recovery"})


def clear_turn_carryover(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop per-turn execution flags when a new user message starts."""
    cleaned = dict(payload)
    for key in _TURN_CARRYOVER_POP:
        cleaned.pop(key, None)
    cleaned["skip_reasoning_after_tools"] = False
    return cleaned


def turn_had_execution(state: AgentState | dict[str, Any], turn_facts: dict[str, Any]) -> bool:
    """True when this round actually ran tools or writing (not merely old artifacts on disk)."""
    if state.get("tool_results"):
        return True
    status = str(state.get("status") or "")
    if status in (TaskStatus.WRITTEN.value, TaskStatus.TOOL_EXECUTED.value):
        return True
    if turn_facts.get("tools_executed"):
        return True
    for action in turn_facts.get("executed_actions") or []:
        text = str(action)
        if text.startswith("writing:") or text.startswith("tool:"):
            return True
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    if intent.get("enabled") and status == TaskStatus.WRITTEN.value:
        return True
    return False


def _inferred_kind_blocks_execution_summary(state: AgentState | dict[str, Any]) -> bool:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    kind = str(audit.get("inferred_kind") or "")
    if kind not in _KINDS_NO_EXECUTION_SUMMARY:
        return False
    confidence = float(audit.get("kind_confidence") or 0.0)
    from app.services.route_audit.config import load_route_audit_config

    return confidence >= load_route_audit_config().min_kind_score


def should_use_execution_summary(
    state: AgentState | dict[str, Any],
    turn_facts: dict[str, Any],
) -> bool:
    """
    Use a short execution recap instead of LLM reasoning only when appropriate.
    """
    payload = state.get("input_payload") or {}
    if not payload.get("skip_reasoning_after_tools"):
        return False
    if payload.get("force_slow_reasoning"):
        return False
    if _inferred_kind_blocks_execution_summary(state):
        return False
    if not turn_had_execution(state, turn_facts):
        return False
    return bool(summary_from_turn_execution(turn_facts, state))


def summary_from_turn_execution(
    turn_facts: dict[str, Any],
    state: AgentState | dict[str, Any] | None = None,
) -> str:
    """Build a one-line recap of tools/writing executed this round (not stale manuscript only)."""
    parts: list[str] = []
    for item in turn_facts.get("tools_executed") or []:
        if item.get("output") is not None:
            parts.append(f"{item['tool']}={item['output']}")
        elif item.get("path"):
            parts.append(f"{item['tool']}→{item['path']}")

    include_manuscript = False
    if state is not None:
        for action in turn_facts.get("executed_actions") or []:
            if str(action).startswith("writing:"):
                include_manuscript = True
                break
        if state.get("tool_results"):
            for item in state.get("tool_results") or []:
                name = str(item.get("tool") or "")
                if "write" in name or "append" in name:
                    include_manuscript = True
                    break

    manuscript = turn_facts.get("manuscript") or {}
    if include_manuscript and manuscript.get("body_path"):
        parts.append(
            f"手稿 {manuscript['body_path']}（{manuscript.get('body_bytes', 0)} 字节）"
        )

    goal = turn_facts.get("goal") or ""
    if not parts:
        return ""
    return " ".join(parts) + (f" 任务：{goal}" if goal else "")


def normalize_reasoning_policy(state: AgentState) -> AgentState:
    """
    After route audit or at turn prep: Q&A / retry-recovery must use full reasoning.
    """
    from app.runtime.state import merge_state

    payload = dict(state.get("input_payload") or {})
    if _inferred_kind_blocks_execution_summary(state):
        payload["skip_reasoning_after_tools"] = False
    audit = payload.get("route_audit") or {}
    kind = str(audit.get("inferred_kind") or "")
    if kind in _KINDS_NO_EXECUTION_SUMMARY:
        intent = payload.get("writing_intent") or {}
        if not intent.get("enabled"):
            payload["skip_reasoning_after_tools"] = False
    return merge_state(state, input_payload=payload)
