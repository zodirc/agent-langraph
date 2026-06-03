"""Detect orphan MISSION_RUNNING (executor gone) and pause for insert + explicit resume."""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.graph_run_registry import is_graph_run_active
from app.services.mission_execution import PAUSE_WORKER_LOST
from app.services.state_store import get_state_store


def should_reconcile_worker_lost(state: AgentState) -> bool:
    """True when persisted mission claims RUNNING but no worker owns the run."""
    if str(state.get("status") or "") != TaskStatus.MISSION_RUNNING.value:
        return False
    if not state.get("mission"):
        return False
    task_id = str(state.get("task_id") or "")
    if not task_id:
        return False
    run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
    run_id = str(run_meta.get("run_id") or "") or None
    return not is_graph_run_active(task_id, run_id)


def reconcile_worker_lost(state: AgentState, *, persist: bool = True) -> AgentState:
    """
    Transition orphan MISSION_RUNNING → MISSION_PAUSED (worker_lost).

    Does not auto-resume; steer on PAUSED applies immediately on next API call.
    """
    if not should_reconcile_worker_lost(state):
        return state
    updated = merge_state(
        state,
        status=TaskStatus.MISSION_PAUSED.value,
        mission_control={
            "done": True,
            "action": "pause",
            "reason": "executor interrupted (worker ended or process restarted)",
            "pause_reason": PAUSE_WORKER_LOST,
        },
        audit_log=append_audit(
            state,
            "mission",
            "worker_lost",
            {"previous_node": state.get("current_node")},
        ),
    )
    if persist:
        get_state_store().save(updated)
    return updated


def load_task_reconciled(task_id: str) -> AgentState | None:
    """Load task state and reconcile worker_lost when needed."""
    stored = get_state_store().load(task_id)
    if not stored:
        return None
    return reconcile_worker_lost(stored)
