"""Planner worker — steer replan only (no reasoning-as-executor for OMAW)."""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.mission_execution import build_mission_checkpoint_summary
from app.services.mission_service import prepare_state_for_mission_act
from app.services.mission_steer_confirm import (
    attach_steer_confirmation_to_state,
    steer_confirmation_pending,
)
from app.services.turn_kind import pipeline_phase_after_planning


def run_planner_worker(state: AgentState) -> AgentState:
    """
    OMAW steer_replan path: planning_node only, then executor or checkpoint.
    Does not terminate mission turns in reasoning_node.
    """
    from app.nodes.planning_node import planning_node
    from app.services.mission_executor import (
        _bootstrap_executor_routing,
        _forced_stop_requested,
        _run_executor_subgraph,
    )

    state = prepare_state_for_mission_act(state)
    if _forced_stop_requested(state):
        return merge_state(state, status="MISSION_PAUSED", current_node="mission_act")

    current = planning_node(state)
    payload = current.get("input_payload") or {}
    if steer_confirmation_pending(payload):
        return attach_steer_confirmation_to_state(current)

    phase = pipeline_phase_after_planning(current)
    if phase == "execute":
        return _run_executor_subgraph(_bootstrap_executor_routing(current))
    if phase == "await_confirm":
        return attach_steer_confirmation_to_state(current)

    checkpoint = build_mission_checkpoint_summary(current)
    return merge_state(
        current,
        status=TaskStatus.MISSION_RUNNING.value,
        current_node="mission_act",
        reasoning_result={
            "summary": str(checkpoint.get("summary") or "规划完成，等待确认或执行"),
            "confidence": 0.85,
            "risk_level": "LOW",
            "structured": {"source": "oma_planner_worker", "phase": phase},
        },
    )
