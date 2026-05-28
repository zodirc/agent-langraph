from __future__ import annotations

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.mission_orchestrator import (
    complete_current_work_item,
    get_current_work_item,
    orchestration_enabled,
)
from app.services.mission_service import update_progress_from_observation
from app.services.observation import attach_observation
from app.services.state_store import get_state_store


def mission_observe_node(state: AgentState) -> AgentState:
    """Build observation snapshot and refresh progress metrics."""
    prev_obs = (state.get("observations") or [])[-1] if state.get("observations") else {}
    state = attach_observation(state)
    obs = state.get("observation") or {}
    from app.services.mission_execution import compute_artifact_delta

    delta = compute_artifact_delta(
        (prev_obs or {}).get("manuscript"),
        obs.get("manuscript"),
    )
    if delta.get("has_change"):
        obs = {**obs, "artifact_delta": delta}
        payload = dict(state.get("input_payload") or {})
        payload["observation"] = obs
        state = merge_state(state, observation=obs, input_payload=payload)
    state = update_progress_from_observation(state)
    from app.services.mission_execution import reconcile_work_plan

    state = reconcile_work_plan(state)
    completed_item = None
    mission = state.get("mission") or {}
    if orchestration_enabled(mission) and not (state.get("observation") or {}).get(
        "has_failures"
    ):
        completed_item = get_current_work_item(state)
        state = complete_current_work_item(state)
        if completed_item:
            from app.services.mission_steer_outcome_confirm import (
                apply_outcome_confirmation_after_work_item,
            )

            state = apply_outcome_confirmation_after_work_item(state, completed_item)
    updated = merge_state(
        state,
        current_node="mission_observe",
        audit_log=append_audit(
            state,
            "mission_observe",
            "success",
            {
                "mission_step": state.get("mission_step"),
                "metrics": (state.get("progress") or {}).get("metrics"),
            },
        ),
    )
    get_state_store().save(updated)
    return updated
