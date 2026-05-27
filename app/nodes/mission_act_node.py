from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.mission_executor import execute_mission_step
from app.services.mission_service import bump_mission_step, prepare_state_for_mission_act
from app.services.state_store import get_state_store


def mission_act_node(state: AgentState) -> AgentState:
    """Execute one mission step (pipeline or subgraph)."""
    decision = state.get("step_decision") or {}
    action = str(decision.get("action", "continue"))

    if action in ("finish", "pause", "escalate"):
        return merge_state(
            state,
            current_node="mission_act",
            audit_log=append_audit(state, "mission_act", "skipped", {"action": action}),
        )

    state = bump_mission_step(state)
    mission_step = int(state.get("mission_step") or 0)
    state = prepare_state_for_mission_act(state)
    try:
        result = execute_mission_step(state, decision)
        micro = None
        from app.services.mission_micro_reflect import maybe_micro_reflect

        reflection = maybe_micro_reflect(result)
        if reflection:
            micro = reflection
            if reflection.get("retry_reasoning") and result.get("reasoning_result"):
                result = merge_state(
                    result,
                    reasoning_result={
                        **(result.get("reasoning_result") or {}),
                        "reflection_note": reflection.get("critique"),
                    },
                )
        result_status = str(result.get("status") or TaskStatus.MISSION_RUNNING.value)
        failed = result_status.endswith("FAILED") or result_status == TaskStatus.FAILED.value
        updated = merge_state(
            result,
            mission_step=int(result.get("mission_step") or mission_step),
            mission_micro_reflect_count=int(state.get("mission_micro_reflect_count") or 0)
            + (1 if micro else 0),
            mission_micro_reflect=micro,
            current_node="mission_act",
            status=result_status if failed else TaskStatus.MISSION_RUNNING.value,
            audit_log=append_audit(
                state,
                "mission_act",
                "error" if failed else "success",
                {
                    "executor": decision.get("next_executor"),
                    "mission_step": int(result.get("mission_step") or mission_step),
                    "micro_reflect": bool(micro),
                    "result_status": result_status,
                },
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        updated = merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"mission_act: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="mission_act",
            audit_log=append_audit(state, "mission_act", "error", {"detail": str(exc)}),
        )
        get_state_store().save(updated)
        return updated
