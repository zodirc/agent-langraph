"""Mission 评估节点
  → mark_mission_phase → status MISSION_RUNNING | MISSION_PAUSED | REASONED
  → route_after_mission_eval → mission_decide | finalize | dead_letter
steer 触发的 pause 可能在此 consume_pending_steer。

mission_eval — program predicates, no LLM.
evaluate_mission_control → mission_control {done, action, reason}"""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.mission_service import mark_mission_phase
from app.services.progress_evaluator import evaluate_mission_control
from app.services.state_store import get_state_store


def mission_eval_node(state: AgentState) -> AgentState:
    """Evaluate termination — program predicates for mission loop."""
    result = evaluate_mission_control(state)
    control = result.to_dict()

    phase = "executing"
    if result.done and result.action == "finish":
        phase = "completed"
    elif result.done and result.action == "pause":
        phase = "paused"

    state = mark_mission_phase(state, phase)
    from app.services.mission_execution import PAUSE_USER_REQUESTED_CANCEL

    status = (
        TaskStatus.MISSION_PAUSED.value
        if phase == "paused"
        else TaskStatus.MISSION_RUNNING.value
    )
    if phase == "paused" and control.get("pause_reason") == PAUSE_USER_REQUESTED_CANCEL:
        status = TaskStatus.CANCELLED.value
    if phase == "completed":
        status = TaskStatus.REASONED.value

    updated = merge_state(
        state,
        mission_control=control,
        status=status,
        current_node="mission_eval",
        audit_log=append_audit(
            state,
            "mission_eval",
            "success",
            control,
        ),
    )
    if (
        result.done
        and result.action == "pause"
        and "steer" in (result.reason or "").lower()
    ):
        from app.services.mission_steer import consume_pending_steer

        updated = consume_pending_steer(updated)
    get_state_store().save(updated)
    return updated
