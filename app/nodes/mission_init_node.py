from __future__ import annotations

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.mission_orchestrator import orchestration_summary
from app.services.mission_service import init_mission_state
from app.services.state_store import get_state_store


def mission_init_node(state: AgentState) -> AgentState:
    """Initialize mission + progress from domain pack."""
    payload = dict(state.get("input_payload") or {})
    if not state.get("mission"):
        state = init_mission_state(state, payload)
    from app.services.turn_contract_lifecycle import reconcile_turn_contract_execution

    state = reconcile_turn_contract_execution(state)
    updated = merge_state(
        state,
        current_node="mission_init",
        audit_log=append_audit(
            state,
            "mission_init",
            "success",
            {
                "kind": (state.get("mission") or {}).get("kind"),
                "execution_mode": (state.get("mission") or {}).get("execution_mode"),
                "orchestration": orchestration_summary(state),
            },
        ),
    )
    get_state_store().save(updated)
    return updated
