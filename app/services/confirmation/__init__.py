"""Human-in-the-loop confirmation gates — registry, preview, block builder."""

from app.services.confirmation.block_builder import (
    build_intent_confirmation_block,
    build_outcome_confirmation_block,
)
from app.services.confirmation.gate_registry import (
    GateContext,
    intent_gate_required,
    outcome_gate_required,
)

__all__ = [
    "GateContext",
    "build_intent_confirmation_block",
    "build_outcome_confirmation_block",
    "intent_gate_required",
    "outcome_gate_required",
]
