"""Hard cancel + run_id fencing (optimization.md §4 / Phase B).

Old run writes are dropped when (run_id, intent_revision) no longer match.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, merge_state
from app.services.graph_run_registry import (
    end_graph_run,
    get_active_run_id,
    is_graph_run_active,
)
from app.services.state_store import get_state_store


class RunCancelled(Exception):
    """Raised when a node attempts work on a cancelled run."""


class RunController:
    @staticmethod
    def cancel(
        state: AgentState,
        *,
        reason: str = "redirect",
        bump_revision: bool = True,
    ) -> AgentState:
        """Hard-cancel active run: mark cancelled, release pool slot, bump revision."""
        task_id = str(state["task_id"])
        run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
        run_id = str(run_meta.get("run_id") or "")
        if run_id:
            end_graph_run(task_id, run_id)

        from app.services.task_control import request_cancel

        try:
            request_cancel(task_id, requested_by="run_controller", reason=reason)
        except Exception:
            pass

        payload = dict(state.get("input_payload") or {})
        if bump_revision:
            from app.services.intent_composer import bump_intent_revision

            payload = bump_intent_revision(payload)

        run_meta = dict(run_meta)
        run_meta["cancelled"] = True
        if run_id:
            run_meta["run_id"] = run_id

        from app.services.session_fsm import FSM_REPLANNING, transition_fsm

        updated = merge_state(
            state,
            input_payload=payload,
            execution_run=run_meta,
        )
        updated = transition_fsm(updated, FSM_REPLANNING)
        get_state_store().save(updated)
        return updated

    @staticmethod
    def assert_run_active(state: AgentState, *, phase: str = "") -> None:
        """Drop stale work: cancelled run or superseded revision."""
        task_id = str(state.get("task_id") or "")
        run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
        if run_meta.get("cancelled"):
            raise RunCancelled(f"run cancelled{f' at {phase}' if phase else ''}")
        run_id = str(run_meta.get("run_id") or "")
        if run_id and task_id and not is_graph_run_active(task_id, run_id):
            revision_at_start = int(run_meta.get("revision_at_start") or 0)
            payload = state.get("input_payload") or {}
            current_revision = int(payload.get("intent_revision") or 0)
            if current_revision > revision_at_start:
                raise RunCancelled(f"run superseded{f' at {phase}' if phase else ''}")

    @staticmethod
    def should_accept_write(state: AgentState) -> bool:
        """True when this run may persist artifact/tool writes."""
        try:
            RunController.assert_run_active(state)
        except RunCancelled:
            return False
        return True

    @staticmethod
    def begin_run(state: AgentState, run_id: str) -> AgentState:
        """Stamp revision_at_start on new run for fencing."""
        payload = state.get("input_payload") or {}
        revision = int(payload.get("intent_revision") or 0)
        run_meta = {
            "run_id": run_id,
            "revision_at_start": revision,
            "cancelled": False,
        }
        from app.services.session_fsm import FSM_RUNNING, is_fsm_replanning, transition_fsm

        updated = merge_state(state, execution_run=run_meta)
        if is_fsm_replanning(updated):
            return updated
        return transition_fsm(updated, FSM_RUNNING)

    @staticmethod
    def active_run_id(task_id: str) -> Optional[str]:
        return get_active_run_id(task_id)
