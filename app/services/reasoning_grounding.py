"""Grounded generation constraints for reasoning node."""

from __future__ import annotations

from app.runtime.evidence_models import AnswerMode
from app.runtime.state import AgentState
from app.services.evidence_pipeline import get_answer_mode


_ANSWER_MODE_INSTRUCTIONS: dict[str, str] = {
    AnswerMode.STRICT_GROUNDED.value: (
        "Answer ONLY using injected evidence. If evidence is insufficient, "
        "state clearly what is missing — do not invent facts."
    ),
    AnswerMode.BEST_EFFORT_GROUNDED.value: (
        "Prefer injected evidence. Mark any inference not directly supported "
        "by evidence as [inference]."
    ),
    AnswerMode.REFUSE_IF_INSUFFICIENT.value: (
        "If key evidence is missing or conflicting, refuse to answer the "
        "factual claim and explain the gap instead of guessing."
    ),
}


def build_grounding_instructions(state: AgentState | dict) -> str:
    """Return answer-mode grounding overlay for reasoning system prompt."""
    mode = get_answer_mode(state)
    base = _ANSWER_MODE_INSTRUCTIONS.get(
        mode, _ANSWER_MODE_INSTRUCTIONS[AnswerMode.BEST_EFFORT_GROUNDED.value]
    )
    conflicts = state.get("evidence_conflicts") or []
    if conflicts:
        base += " Conflicting evidence was detected — note conflicts explicitly."
    packets = state.get("evidence_packets") or []
    if packets:
        base += f" {len(packets)} evidence packet(s) are available; cite [doc_id] when possible."
    return base
