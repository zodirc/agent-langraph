"""
Intent Composer — arbitrate control-plane tokens before planning / mechanical steps.

Priority (high → low): forced intervention → pending steer planning → active turn_contract
→ agenda head → execution_grant (scope=mechanical_resume only) → step_policy default.

No NLP / keyword tables; decisions use payload flags and manuscript facts only.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState

GRANT_SCOPE_MECHANICAL_RESUME = "mechanical_resume"
GRANT_SCOPE_APPROVE_GATE = "approve_gate"
GRANT_SCOPE_EXPLICIT_CONTINUE = "explicit_continue"

_MECHANICAL_GRANT_SOURCES = frozenset(
    {
        "resume_api",
        "continue_signal",
        "explicit_request",
        "intervention",
    }
)


def _grant_block(payload: dict[str, Any]) -> dict[str, Any]:
    grant = payload.get("execution_grant")
    return grant if isinstance(grant, dict) else {}


def steer_planning_pending(payload: dict[str, Any]) -> bool:
    """True while steer or contract invalidation still requires a planning pass."""
    from app.services.mission_steer import steer_requires_planning

    return steer_requires_planning(payload)


def grant_scope(payload: dict[str, Any]) -> str:
    block = _grant_block(payload)
    if not block:
        return ""
    return str(block.get("scope") or GRANT_SCOPE_MECHANICAL_RESUME)


def resolve_grant_scope(*, source: str) -> str:
    src = str(source or "")
    if src in _MECHANICAL_GRANT_SOURCES or src.startswith("pattern_kind"):
        return GRANT_SCOPE_MECHANICAL_RESUME
    if src in ("approve_gate", "gate_confirm"):
        return GRANT_SCOPE_APPROVE_GATE
    return GRANT_SCOPE_EXPLICIT_CONTINUE


def grant_may_mechanical_forward(
    payload: dict[str, Any],
    *,
    state: Optional[AgentState] = None,
) -> bool:
    """
    Whether execution_grant may skip planning and apply step_policy / grant_forward.

    Blocked when steer planning is pending or grant scope is not mechanical resume.
    Contract invalidation from the grant itself does not block the same grant consume.
    """
    block = _grant_block(payload)
    if not block or not block.get("consume_once", True):
        return False
    if payload.get("require_planning_after_steer") and not payload.get("steer_planning_done"):
        return False
    if payload.get("steer_applied_at") and not payload.get("steer_planning_done"):
        return False
    scope = grant_scope(payload)
    if scope != GRANT_SCOPE_MECHANICAL_RESUME:
        return False
    if state is not None:
        from app.services.mission_intervention import intervention_from_payload, is_forced

        intervention = intervention_from_payload(payload) or {}
        if intervention and is_forced(intervention):
            material = frozenset(
                {
                    "rewrite_outline",
                    "reset_body",
                    "edit_plot",
                    "run_tools",
                    "enqueue_work",
                    "batch_unit_quality",
                }
            )
            if str(intervention.get("action") or "") in material:
                return False
    return True


def may_issue_execution_grant(payload: dict[str, Any]) -> bool:
    """Whether a new mechanical grant may be written onto payload."""
    return not steer_planning_pending(payload) and not (
        payload.get("steer_applied_at") and not payload.get("steer_planning_done")
    )


def compose_intent_revision(payload: dict[str, Any]) -> int:
    """Monotonic intent revision for turn_contract binding."""
    return int(payload.get("intent_revision") or 0) + 1


def bump_intent_revision(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["intent_revision"] = compose_intent_revision(out)
    return out


def record_grant_steer_conflict(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Audit when grant was suppressed because steer planning takes precedence."""
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_contract_event("grant_steer_conflict")
    out = dict(payload)
    out.pop("execution_grant", None)
    out["grant_steer_conflict"] = {
        "reason": str(reason),
        "had_grant": bool(_grant_block(payload)),
    }
    return out
