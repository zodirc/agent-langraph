"""Intent snapshot invalidation on artifact / confirmation side effects."""

from __future__ import annotations

from app.runtime.state import AgentState


_ARTIFACT_MUTATORS = frozenset(
    {
        "write_text_artifact",
        "append_text_artifact",
        "edit_text_artifact",
    }
)


def maybe_invalidate_intent_on_tool_result(
    state: AgentState,
    tool_name: str,
    result: dict,
) -> AgentState:
    """Invalidate frozen intent when tools mutate artifacts outside revision dry-run preview."""
    if str(tool_name) not in _ARTIFACT_MUTATORS:
        return state
    if str(result.get("status") or "") not in ("ok", "success"):
        return state
    body = result.get("result") or {}
    if body.get("dry_run"):
        return state
    from app.services.intent_snapshot import invalidate_intent_snapshot

    return invalidate_intent_snapshot(state, f"tool_side_effect:{tool_name}")


def maybe_invalidate_intent_on_steer_confirm(state: AgentState) -> AgentState:
    """Confirmation card rewrite invalidates frozen observation."""
    payload = state.get("input_payload") or {}
    if not payload.get("steer_intent_confirmed"):
        return state
    from app.services.intent_snapshot import invalidate_intent_snapshot

    return invalidate_intent_snapshot(state, "steer_intent_confirmed")
