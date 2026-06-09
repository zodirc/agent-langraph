"""Detect reasoning answers that narrate edits without executing tools."""

from __future__ import annotations

import re

_PROSE_EDIT_RE = re.compile(
    r"(?:```\s*edit\b|edit_text_artifact\s*[:：])",
    re.IGNORECASE,
)


def reasoning_claims_unexecuted_edit(summary: str) -> bool:
    """True when the model describes edit_text_artifact in prose instead of running it."""
    text = (summary or "").strip()
    if not text:
        return False
    return bool(_PROSE_EDIT_RE.search(text))


def reasoning_terminal_blocked_reason(state: dict) -> str | None:
    """Return a short reason when reasoning must not end the turn."""
    from app.runtime.state import AgentState
    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
        validate_turn_contract_execution,
    )
    from app.services.turn_kind import should_use_reasoning_terminal

    payload = state.get("input_payload") or {}
    if not isinstance(state, dict):
        return None
    typed_state = state  # type: ignore[assignment]
    if not should_use_reasoning_terminal(typed_state):  # type: ignore[arg-type]
        issues = validate_turn_contract_execution(typed_state)  # type: ignore[arg-type]
        if issues:
            return issues[0]
        if contract_requires_side_effects(payload, state=typed_state) and not is_turn_contract_fulfilled(typed_state):  # type: ignore[arg-type]
            return "contract_unfulfilled"
    reasoning = state.get("reasoning_result") or {}
    summary = str(reasoning.get("summary") or "")
    if (
        contract_requires_side_effects(payload, state=typed_state)  # type: ignore[arg-type]
        and reasoning_claims_unexecuted_edit(summary)
    ):
        return "narrated_edit_without_tool"
    return None
