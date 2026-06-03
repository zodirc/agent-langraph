"""Supervisor Worker 节点
    → create_initial_state → run_worker_graph (retrieval→tool→reasoning)
  聚合 worker_results + tool_results → status REASONED

supervisor_worker — parallel domain workers.
run_workers_parallel (worker_executor.py):
  per subtask → execute_domain_worker_with_retry
Next: supervisor_merge_node"""

from __future__ import annotations

from typing import Any

from app.domain.worker_executor import run_workers_parallel
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def supervisor_worker_node(state: AgentState) -> AgentState:
    """
    Supervisor: execute all subtasks via domain workers (parallel).

    Writes: worker_results, tool_results, status, audit_log
    """
    try:
        subtasks = list(state.get("subtasks") or [])
        results, updated_subtasks, errors, all_tool_results = run_workers_parallel(
            parent_task_id=state["task_id"],
            user_id=state["user_id"],
            subtasks=subtasks,
            context=state.get("input_payload", {}),
        )

        updated = merge_state(
            state,
            subtasks=updated_subtasks,
            worker_results=results,
            tool_results=all_tool_results or None,
            errors=list(state.get("errors", [])) + errors,
            status=TaskStatus.REASONED.value,
            current_node="supervisor_worker",
            audit_log=append_audit(
                state,
                "supervisor_worker",
                "success",
                {
                    "workers_run": len(subtasks),
                    "parallel": True,
                    "failed": sum(1 for s in updated_subtasks if s.get("status") == "FAILED"),
                },
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"supervisor_worker: {exc}"],
            status=TaskStatus.FAILED.value,
            current_node="supervisor_worker",
            audit_log=append_audit(state, "supervisor_worker", "error", {"detail": str(exc)}),
        )
