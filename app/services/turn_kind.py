"""
Turn kind — explicit turn classification (no goal keyword tables).

unified-core WP-6: mission/steer kinds removed; a turn either needs the
executor (side-effect contract) or terminates in reasoning.
"""

from __future__ import annotations

from typing import Any, Literal

from app.runtime.state import AgentState

TurnKind = Literal[
    "execute",
    "side_qa",
    "narrate_only",
]

_NARRATOR_TERMINAL_KINDS = frozenset({"side_qa", "narrate_only"})


def stamp_turn_kind(payload: dict[str, Any], kind: TurnKind) -> dict[str, Any]:
    out = dict(payload)
    out["turn_kind"] = kind
    return out


def resolve_turn_kind(state: AgentState) -> TurnKind:
    """Resolve turn kind from explicit payload field and structural flags."""
    payload = state.get("input_payload") or {}
    explicit = str(payload.get("turn_kind") or "").strip()
    if explicit in ("execute", "side_qa", "narrate_only"):
        return explicit  # type: ignore[return-value]

    from app.services.turn_contract import contract_requires_side_effects

    if contract_requires_side_effects(payload, state=state):
        return "execute"

    return "narrate_only"


def contract_needs_executor(state: AgentState) -> bool:
    from app.services.turn_contract import contract_requires_side_effects

    return contract_requires_side_effects(state.get("input_payload") or {}, state=state)


def pipeline_phase_after_planning(state: AgentState) -> str:
    """Next phase for the pipeline after planning_node: execute | narrate."""
    if contract_needs_executor(state):
        return "execute"
    return "narrate"


_ANSWER_ONLY_ACTION_TYPES = frozenset({"answer", "retrieve"})


def is_narrate_answer_turn_ready(state: AgentState | dict[str, Any]) -> bool:
    """True when a QA/narrate turn already has a user-facing answer and needs no executor.

    Used to skip post-answer replan loops and emit early ``delivered`` for streaming UX.
    """
    from app.services.turn_contract import contract_requires_side_effects

    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state):  # type: ignore[arg-type]
        return False
    if resolve_turn_kind(state) not in _NARRATOR_TERMINAL_KINDS:  # type: ignore[arg-type]
        thin = str(payload.get("thin_execution_profile") or "")
        if thin != "qa_direct":
            return False

    reasoning = state.get("reasoning_result") or {}
    answer_text = ""
    if isinstance(reasoning, dict):
        answer_text = str(reasoning.get("summary") or reasoning.get("answer") or "")
    if not answer_text.strip():
        answer_text = str(state.get("final_answer") or "")
    if not answer_text.strip():
        return False

    actions = [a for a in (state.get("planned_actions") or []) if isinstance(a, dict)]
    if actions:
        types = {str(a.get("type") or "") for a in actions}
        if types - _ANSWER_ONLY_ACTION_TYPES:
            return False

    from app.services.converge import NEXT_PROCEED, evaluate_convergence

    conv = evaluate_convergence(state)
    return bool(conv.done and conv.next == NEXT_PROCEED and conv.reason == "answer_ready")


def should_use_reasoning_terminal(state: AgentState) -> bool:
    """Reasoning may only terminate a turn when executor work is done or turn is narrate-only."""
    if pipeline_phase_after_planning(state) == "execute":
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
    """Authoritative plan lines for trace/UI (contract, not LLM prose)."""
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

    if not lines:
        return list(state.get("plan") or [])[:8]
    return lines
