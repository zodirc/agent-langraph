"""
Structured steer confirmation — no natural-language phrase matching.

Clients approve pending intent/outcome gates via:
  POST /tasks/{id}/resume  {"confirm": true}
  POST /tasks/{id}/steer   {"confirm": true}
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState


def build_confirmation_actions(task_id: str) -> dict[str, Any]:
    return {
        "resume": {
            "method": "POST",
            "path": f"/tasks/{task_id}/resume",
            "body": {"confirm": True},
        },
        "steer": {
            "method": "POST",
            "path": f"/tasks/{task_id}/steer",
            "body": {"confirm": True},
        },
        "cli_alias": "/confirm",
    }


def enrich_confirmation_block(task_id: str, block: dict[str, Any]) -> dict[str, Any]:
    from app.services.client_display import confirmation_panel_display

    phase = str(block.get("phase") or "intent")
    return {
        **block,
        "user_actions": build_confirmation_actions(task_id),
        "display": confirmation_panel_display(phase),
    }


def confirmation_actions_hint(actions: dict[str, Any]) -> str:
    resume = actions.get("resume") or {}
    steer = actions.get("steer") or {}
    return (
        f"Approve: {resume.get('method', 'POST')} {resume.get('path', '')} "
        f"body {resume.get('body', {'confirm': True})}; "
        f"or {steer.get('method', 'POST')} {steer.get('path', '')} "
        f"body {steer.get('body', {'confirm': True})}. "
        f"Web CLI alias: {actions.get('cli_alias', '/confirm')}."
    )


def steer_confirmation_pending_any(payload: dict[str, Any]) -> bool:
    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending

    return steer_confirmation_pending(payload) or steer_outcome_confirmation_pending(payload)


def try_apply_structured_confirm(
    state: AgentState,
    *,
    confirm: bool,
) -> Optional[AgentState]:
    """Apply confirm:true when a gate is pending; return None if not applicable."""
    if not confirm:
        return None
    payload = state.get("input_payload") or {}
    from app.services.mission_steer_confirm import (
        confirm_steer_intent,
        steer_confirmation_pending,
    )
    from app.services.mission_steer_outcome_confirm import (
        confirm_steer_outcome,
        steer_outcome_confirmation_pending,
    )

    if steer_confirmation_pending(payload):
        return confirm_steer_intent(state)
    if steer_outcome_confirmation_pending(payload):
        return confirm_steer_outcome(state)
    return None


def confirmation_sse_fields(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not steer_confirmation_pending_any(payload):
        return {"confirmation_actions": None}
    return {"confirmation_actions": build_confirmation_actions(task_id)}
