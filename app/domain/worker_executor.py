from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from app.config.settings import settings
from app.domain.packs.registry import get_domain_pack, list_pack_names
from app.runtime.state import AgentState, TaskStatus, create_initial_state, merge_state
from app.runtime.worker_graph import run_worker_graph


def execute_domain_worker(
    *,
    parent_task_id: str,
    user_id: str,
    domain: str,
    description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run domain worker via LangGraph subgraph (architecture §20.3)."""
    pack = get_domain_pack(domain)
    subtask_id = str(uuid.uuid4())
    worker_state = create_initial_state(
        task_id=f"{parent_task_id}:{subtask_id}",
        user_id=user_id,
        task_type=f"worker:{domain}",
        input_payload={
            "goal": description,
            "domain": domain,
            "context": context or {},
            "risk_level": pack.risk_level,
            "needs_search": True,
            "use_tools": bool(pack.tools),
        },
    )
    worker_state = merge_state(
        worker_state,
        execution_mode="worker",
        plan=pack.planning_hints,
        selected_tools=pack.tools if pack.tools else None,
        status=TaskStatus.PLANNED.value,
        current_node="worker",
    )

    try:
        worker_state = run_worker_graph(worker_state)
        reasoning = worker_state.get("reasoning_result") or {}
        status = (
            "COMPLETED"
            if worker_state.get("status") in (TaskStatus.REASONED.value, TaskStatus.COMPLETED.value)
            else "FAILED"
        )
        return {
            "subtask_id": subtask_id,
            "domain": domain,
            "status": status,
            "summary": reasoning.get("summary", ""),
            "confidence": reasoning.get("confidence", 0.0),
            "risk_level": reasoning.get("risk_level", pack.risk_level),
            "structured": reasoning.get("structured", {}),
            "tool_results": worker_state.get("tool_results"),
            "knowledge_count": len(worker_state.get("retrieved_knowledge") or []),
            "node_history": worker_state.get("node_history", []),
        }
    except Exception as exc:
        return {
            "subtask_id": subtask_id,
            "domain": domain,
            "status": "FAILED",
            "summary": "",
            "error": str(exc),
        }


def execute_domain_worker_with_retry(
    *,
    parent_task_id: str,
    user_id: str,
    domain: str,
    description: str,
    context: dict[str, Any] | None = None,
    max_retries: Optional[int] = None,
) -> dict[str, Any]:
    """Worker execution with supervisor-level retry (§20.2)."""
    retries = max_retries if max_retries is not None else settings.SUPERVISOR_WORKER_RETRIES
    last: dict[str, Any] = {}
    for attempt in range(retries + 1):
        last = execute_domain_worker(
            parent_task_id=parent_task_id,
            user_id=user_id,
            domain=domain,
            description=description,
            context=context,
        )
        if last.get("status") == "COMPLETED":
            last["attempts"] = attempt + 1
            return last
    last["attempts"] = retries + 1
    last["status"] = "FAILED"
    return last


def run_workers_parallel(
    *,
    parent_task_id: str,
    user_id: str,
    subtasks: list[dict[str, Any]],
    context: dict[str, Any],
    max_workers: Optional[int] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Execute subtasks in parallel; returns results, updated subtasks, errors, tool_results."""
    workers = max_workers or settings.SUPERVISOR_MAX_WORKERS
    results: dict[str, Any] = {}
    errors: list[str] = []
    all_tool_results: list[dict[str, Any]] = []
    updated_subtasks = [dict(item) for item in subtasks]

    def _run_one(subtask: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        outcome = execute_domain_worker_with_retry(
            parent_task_id=parent_task_id,
            user_id=user_id,
            domain=subtask["domain"],
            description=subtask["description"],
            context=context,
        )
        return subtask["subtask_id"], outcome

    use_a2a = any(st.get("a2a_message") or st.get("required_capability") for st in updated_subtasks)
    if use_a2a:
        from app.services.a2a_dispatch import run_subtasks_via_a2a

        return run_subtasks_via_a2a(
            parent_task_id=parent_task_id,
            user_id=user_id,
            subtasks=updated_subtasks,
            context=context,
            max_workers=workers,
        )

    with ThreadPoolExecutor(max_workers=min(workers, max(len(subtasks), 1))) as pool:
        futures = {pool.submit(_run_one, st): st for st in updated_subtasks}
        for future in as_completed(futures):
            subtask = futures[future]
            subtask_id, outcome = future.result()
            results[subtask_id] = outcome
            subtask["status"] = outcome.get("status", "FAILED")
            subtask["attempts"] = outcome.get("attempts", 1)
            if outcome.get("tool_results"):
                all_tool_results.extend(outcome["tool_results"])
            if outcome.get("status") == "FAILED":
                errors.append(
                    f"worker {subtask['domain']}: {outcome.get('error', 'failed after retries')}"
                )

    return results, updated_subtasks, errors, all_tool_results


def decompose_task(goal: str, domains: list[str] | None = None) -> list[dict[str, Any]]:
    """Supervisor decomposition via A2A capability-based messages."""
    from app.services.a2a_dispatch import decompose_to_agent_messages

    return decompose_to_agent_messages(goal, domains)
