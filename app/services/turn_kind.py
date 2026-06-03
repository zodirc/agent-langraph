"""
Turn kind — explicit turn classification (no goal keyword tables).

Drives whether mission_act may end in reasoning, must run executor, or pause for confirm.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from app.runtime.state import AgentState

TurnKind = Literal[
    "steer_replan",
    "steer_execute",
    "mission_step_execute",
    "mechanical_continue",
    "side_qa",
    "narrate_only",
]

_EXECUTOR_WORK_ITEM_KINDS = frozenset(
    {
        "review_chapter",
        "polish_chapter",
        "chapter_summary",
        "consistency_check",
        "append_body",
        "append_chapter",
        "write_body",
        "write_outline",
        "reset_body",
        "edit_plot",
        "run_tools",
    }
)

_NARRATOR_TERMINAL_KINDS = frozenset({"side_qa", "narrate_only"})


def stamp_turn_kind(payload: dict[str, Any], kind: TurnKind) -> dict[str, Any]:
    out = dict(payload)
    out["turn_kind"] = kind
    return out


def resolve_turn_kind(state: AgentState) -> TurnKind:
    """Resolve turn kind from explicit payload field and structural flags."""
    payload = state.get("input_payload") or {}
    explicit = str(payload.get("turn_kind") or "").strip()
    if explicit in (
        "steer_replan",
        "steer_execute",
        "mission_step_execute",
        "mechanical_continue",
        "side_qa",
        "narrate_only",
    ):
        return explicit  # type: ignore[return-value]

    if payload.get("mission_suspended") or not state.get("mission"):
        return "side_qa"

    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        if payload.get("steer_planning_done"):
            return "steer_execute"
        return "steer_replan"

    from app.services.intent_composer import grant_may_mechanical_forward

    if grant_may_mechanical_forward(payload, state=state):
        return "mechanical_continue"

    if payload.get("steer_applied_at") and payload.get("steer_planning_done"):
        return "steer_execute"

    if agenda_has_executor_pending(state):
        return "mission_step_execute"

    from app.services.turn_contract import contract_requires_side_effects

    if contract_requires_side_effects(payload, state=state):
        return "mission_step_execute"

    return "narrate_only"


def agenda_has_executor_pending(state: AgentState) -> bool:
    """True when work_plan has a runnable non-terminal work item."""
    from app.services.mission_orchestrator import get_current_work_item

    item = get_current_work_item(state)
    if not item:
        return False
    return str(item.get("kind") or "") in _EXECUTOR_WORK_ITEM_KINDS


def contract_needs_executor(state: AgentState) -> bool:
    from app.services.turn_contract import contract_requires_side_effects

    if not contract_requires_side_effects(
        state.get("input_payload") or {}, state=state
    ):
        return False
    if agenda_has_executor_pending(state):
        return True
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    return bool(intent.get("enabled"))


def pipeline_phase_after_planning(state: AgentState) -> str:
    """
    Next phase for run_pipeline_request after planning_node.

    Returns: await_confirm | execute | narrate | steer_replan_only
    """
    from app.services.mission_steer_confirm import steer_confirmation_pending

    payload = state.get("input_payload") or {}
    if steer_confirmation_pending(payload):
        return "await_confirm"

    if contract_needs_executor(state):
        return "execute"

    kind = resolve_turn_kind(state)
    if kind == "steer_replan" and not payload.get("steer_planning_done"):
        return "steer_replan_only"

    if kind in _NARRATOR_TERMINAL_KINDS:
        return "narrate"

    if agenda_has_executor_pending(state):
        return "execute"

    return "narrate"


def should_use_reasoning_terminal(state: AgentState) -> bool:
    """Reasoning may only terminate a turn when executor work is done or turn is narrate-only."""
    if pipeline_phase_after_planning(state) == "execute":
        return False
    if pipeline_phase_after_planning(state) == "await_confirm":
        return False
    if pipeline_phase_after_planning(state) == "steer_replan_only":
        return False

    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
    )

    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state) and not is_turn_contract_fulfilled(
        state
    ):
        return False
    return True


def plan_steps_for_display(state: AgentState) -> list[str]:
    """Authoritative plan lines for trace/UI (contract + agenda, not LLM prose)."""
    from app.services.turn_contract import contract_from_payload

    payload = state.get("input_payload") or {}
    lines: list[str] = []
    contract = contract_from_payload(payload)
    if contract:
        op = str(contract.get("primary_op") or "")
        if op:
            lines.append(f"contract: {op}")
        reason = str(contract.get("user_visible_reason") or "").strip()
        if reason:
            lines.append(reason)

    from app.services.mission_orchestrator import get_current_work_item

    item = get_current_work_item(state)
    if item:
        lines.append(
            f"agenda: {item.get('kind')} — {item.get('title') or item.get('id')}"
        )

    if not lines:
        return list(state.get("plan") or [])[:8]
    return lines
