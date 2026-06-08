"""Control-plane payload hygiene (optimization.md Phase D).

Client-visible routing hints (preempt, replace_goal, …) must not drive backend
routing — only ``fsm_state`` and ``classify_user_event`` do.  Legacy steer flags
are telemetry derived from FSM transitions, not independent routing inputs.
"""

from __future__ import annotations

from typing import Any

# Legacy replan flags — written only by FSM transitions / steer lifecycle.
LEGACY_REPLAN_FLAG_KEYS: frozenset[str] = frozenset(
    {
        "require_planning_after_steer",
        "steer_planning_done",
        "foreground_replan_dispatch",
        "pending_replan",
    }
)

# Keys clients must never use to steer routing (Phase A/D unified ingress).
CLIENT_ROUTING_HINT_KEYS: frozenset[str] = frozenset(
    {
        "preempt",
        "replace_goal",
        "foreground_preempt_pending",
        "steer_replan_mode",
        "steer_requires_planning",
        *LEGACY_REPLAN_FLAG_KEYS,
    }
)


def strip_client_routing_hints(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Remove client-supplied control hints before classify / graph execution."""
    out = dict(payload or {})
    for key in CLIENT_ROUTING_HINT_KEYS:
        out.pop(key, None)
    return out


def merge_stripped_message_payload(
    base: dict[str, Any] | None,
    *,
    text: str,
    meta: dict[str, Any] | None = None,
    confirm: bool = False,
    intervention: dict[str, Any] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Build inbound message payload without client routing hints."""
    merged = strip_client_routing_hints(base)
    if text:
        merged["goal"] = text
        merged["message"] = text
    if meta:
        prev = merged.get("meta") if isinstance(merged.get("meta"), dict) else {}
        merged["meta"] = {**prev, **dict(meta)}
    if confirm:
        merged["confirm"] = True
    if intervention:
        merged["intervention"] = intervention
    if priority:
        merged["priority"] = int(priority)
    return merged
