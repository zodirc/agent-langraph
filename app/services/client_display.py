"""
Server-composed UI hints for Web/API clients.

Clients should render `system_lines` and structured `display` / `autonomous_ui`
instead of branching on intervention action names in the browser.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState
from app.services.mission_intervention import intervention_from_payload
from app.services.mission_steer_confirm import steer_confirmation_pending
from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending


def _orchestration_line(state: AgentState) -> str:
    from app.services.mission_orchestrator import orchestration_detail, orchestration_summary

    summary = orchestration_summary(state)
    detail = orchestration_detail(state)
    if not summary and not detail:
        return ""
    parts = [summary] if summary else []
    if isinstance(detail, dict):
        done = detail.get("done", 0)
        total = detail.get("total", 0)
        completed = detail.get("completed") or []
        cur = detail.get("current_title") or detail.get("current_kind")
        cur_status = detail.get("current_status")
        completed_s = "、".join(str(x) for x in completed) if completed else "—"
        current_s = "—"
        if cur:
            current_s = f"{cur}（{cur_status}）" if cur_status and cur_status != "done" else str(cur)
        parts.append(f"orchestration {done}/{total} done=[{completed_s}] current=[{current_s}]")
    return " | ".join(parts)


def _intervention_display(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    block = intervention_from_payload(payload)
    if not block:
        return None
    return {
        "action": block.get("action"),
        "force": bool(block.get("force")),
        "reason": str(block.get("reason") or "").strip() or None,
    }


def _pause_context_lines(state: AgentState, control: dict[str, Any]) -> list[str]:
    """User-visible lines from LLM/planner fields, not client-side action templates."""
    payload = state.get("input_payload") or {}
    lines: list[str] = []
    intervention = _intervention_display(payload)
    if intervention and intervention.get("reason"):
        lines.append(intervention["reason"])
    elif intervention and intervention.get("action"):
        lines.append(f"mission_intervention.action={intervention['action']}")

    confirm_block = payload.get("steer_intent_confirmation") or {}
    if steer_confirmation_pending(payload) and confirm_block.get("summary_text"):
        first = str(confirm_block["summary_text"]).strip().split("\n")[0]
        if first and first not in lines:
            lines.append(first)

    reason = str(control.get("reason") or "").strip()
    if reason and reason not in lines:
        lines.append(reason)

    orch = _orchestration_line(state)
    if orch:
        lines.append(orch)
    return lines


def _payload_for_gates(state: AgentState) -> dict[str, Any]:
    from app.services.state_store import get_state_store, merge_input_payload_for_gates

    stored = get_state_store().load(state["task_id"], read_only=True)
    return merge_input_payload_for_gates(state, stored or state)


def _consecutive_failures_paused(state: AgentState, control: dict[str, Any]) -> bool:
    if "consecutive_failures" in str(control.get("reason") or ""):
        return True
    progress = state.get("progress") or {}
    failures = int(progress.get("consecutive_failures") or 0)
    if failures <= 0:
        return False
    from app.config.settings import settings

    mission = state.get("mission") or {}
    budget = mission.get("budget") or {}
    max_failures = int(
        budget.get("max_failures") or getattr(settings, "MISSION_MAX_FAILURES", 3)
    )
    return failures >= max_failures


def autonomous_ui_for_pause(state: AgentState, *, autonomous: bool, steer_pause: bool) -> dict[str, Any]:
    payload = _payload_for_gates(state)
    control = state.get("mission_control") or {}
    if not autonomous:
        return {"enabled": False, "behavior": None, "system_lines": []}

    lines: list[str] = []
    behavior: Optional[str] = None
    resume_confirm = False

    if steer_outcome_confirmation_pending(payload):
        behavior = "wait_outcome_confirm"
        lines.append("autonomous: paused until outcome gate approved (confirm:true on resume/steer)")
    elif steer_confirmation_pending(payload):
        behavior = "wait_intent_confirm"
        lines.append("autonomous: paused until intent gate approved (confirm:true on resume/steer)")
    elif payload.get("steer_review_outline"):
        behavior = "steer_review_only"
        lines.append("autonomous: review_outline — resume when ready to read outline")
    elif _consecutive_failures_paused(state, control):
        behavior = "failure_pause"
        lines.append(
            "autonomous: paused after repeated step failures — steer or resume manually when ready"
        )
    elif steer_pause:
        behavior = "resume_after_steer"
        lines.append("autonomous: steer applied — call resume to continue planning/execution")
    elif not steer_pause:
        behavior = "auto_resume_step"
        lines.append("autonomous: auto-resume next work item")

    if behavior in ("wait_outcome_confirm", "wait_intent_confirm"):
        resume_confirm = True

    if behavior == "failure_pause":
        resume_confirm = False

    return {
        "enabled": True,
        "behavior": behavior,
        "resume_confirm": resume_confirm,
        "system_lines": lines,
    }


def build_mission_paused_payload(
    state: AgentState,
    *,
    autonomous: bool,
    steer_pause: bool,
    confirmation_actions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """SSE mission_paused body — render system_lines on the client."""
    payload = _payload_for_gates(state)
    control = state.get("mission_control") or {}
    intervention = _intervention_display(payload)

    system_lines = _pause_context_lines(state, control)
    if not system_lines:
        system_lines = ["mission_paused"]

    auto_ui = autonomous_ui_for_pause(state, autonomous=autonomous, steer_pause=steer_pause)

    return {
        "task_id": state["task_id"],
        "status": state.get("status"),
        "autonomous": autonomous,
        "steer_pause": steer_pause,
        "steer_review_only": bool(payload.get("steer_review_outline")),
        "steer_intent_pending_confirm": steer_confirmation_pending(payload),
        "steer_intent_confirmation": payload.get("steer_intent_confirmation"),
        "steer_outcome_pending_confirm": steer_outcome_confirmation_pending(payload),
        "steer_outcome_confirmation": payload.get("steer_outcome_confirmation"),
        "confirmation_actions": confirmation_actions,
        "display": {
            "pause_kind": "steer" if steer_pause else "stepwise",
            "mission_control": control,
            "intervention": intervention,
            "orchestration_summary": _orchestration_line(state),
        },
        "system_lines": system_lines,
        "autonomous_ui": auto_ui,
        # Legacy flags for older clients (avoid new branching in JS)
        "steer_action": (intervention or {}).get("action"),
        "orchestration": None,
        "orchestration_detail": None,
    }


def build_steer_task_client_display(state: AgentState, *, queued: bool) -> dict[str, Any]:
    payload = state.get("input_payload") or {}
    pending = state.get("pending_user_message") or {}
    depth = len((pending.get("messages") if isinstance(pending, dict) else []) or [])
    intervention = _intervention_display(payload)

    if queued:
        lines = [
            "steer_queued: applied after current mission step completes",
            f"queue_depth={depth}",
        ]
    else:
        lines = ["steer_applied: merged into task state"]
        if intervention and intervention.get("reason"):
            lines.append(intervention["reason"])
        elif intervention and intervention.get("action"):
            lines.append(f"mission_intervention.action={intervention['action']}")

    return {
        "kind": "steer_queued" if queued else "steer_applied",
        "system_lines": lines,
        "display": {
            "queued": queued,
            "queue_depth": depth,
            "status": state.get("status"),
            "intervention": intervention,
        },
    }


def confirmation_panel_display(phase: str) -> dict[str, str]:
    titles = {
        "intent": "steer_intent_confirmation",
        "outcome": "steer_outcome_confirmation",
    }
    return {"phase": phase, "title": titles.get(phase, "steer_confirmation")}
