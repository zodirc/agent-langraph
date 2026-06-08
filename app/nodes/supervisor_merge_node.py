"""Supervisor 合并节点
_retry_failed_workers: 关键失败子任务补偿一次
全失败 → degraded reasoning_result（无 LLM）
否则 invoke_structured(REASONING_SYSTEM) + worker summaries fallback
→ reasoning_result → route_after_policy (supervisor_graph 共享收尾)

supervisor_merge — synthesize worker outputs."""

from __future__ import annotations

import json
from typing import Any

from app.config.prompts import REASONING_SYSTEM
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.llm_client import invoke_structured
from app.services.state_store import get_state_store


def _build_merge_context(state: AgentState) -> str:
    from app.services.conversation_context import (
        conversation_history_for_llm,
        conversation_history_from_state,
    )

    worker_results = state.get("worker_results") or {}
    return json.dumps(
        {
            "goal": state.get("input_payload", {}).get("goal"),
            "subtasks": state.get("subtasks", []),
            "worker_results": worker_results,
            "conversation_history": conversation_history_for_llm(
                conversation_history_from_state(state)
            ),
        },
        ensure_ascii=False,
    )


def _retry_failed_workers(state: AgentState) -> AgentState:
    """Compensate: re-dispatch failed critical subtasks once."""
    subtasks = list(state.get("subtasks") or [])
    worker_results = dict(state.get("worker_results") or {})
    failed_critical = [
        s
        for s in subtasks
        if s.get("status") == "FAILED" and s.get("critical", True)
        and not s.get("compensation_attempted")
    ]
    if not failed_critical:
        return state
    from app.domain.worker_executor import run_workers_parallel

    retried, updated_subtasks, errors, tool_results = run_workers_parallel(
        parent_task_id=state["task_id"],
        user_id=state["user_id"],
        subtasks=failed_critical,
        context=state.get("input_payload", {}),
    )
    for sid, outcome in retried.items():
        worker_results[sid] = outcome
    for s in updated_subtasks:
        s["compensation_attempted"] = True
    all_subtasks = []
    failed_ids = {s.get("subtask_id") for s in failed_critical}
    for s in subtasks:
        if s.get("subtask_id") in failed_ids:
            repl = next((x for x in updated_subtasks if x.get("subtask_id") == s.get("subtask_id")), s)
            all_subtasks.append(repl)
        else:
            all_subtasks.append(s)
    return merge_state(
        state,
        subtasks=all_subtasks,
        worker_results=worker_results,
        tool_results=(state.get("tool_results") or []) + (tool_results or []),
        errors=list(state.get("errors", [])) + errors,
    )


def supervisor_merge_node(state: AgentState) -> AgentState:
    """
    Supervisor: synthesize worker outputs into reasoning_result.

    Writes: reasoning_result, structured_output, status, audit_log
    """
    try:
        state = _retry_failed_workers(state)
        worker_results = state.get("worker_results") or {}
        if worker_results and all(
            str(v.get("status", "")).upper() == "FAILED" for v in worker_results.values()
        ):
            summaries = [
                f"[{k}] failed: {v.get('error', v.get('summary', ''))}"
                for k, v in worker_results.items()
            ]
            reasoning_result = {
                "summary": "Supervisor degraded merge:\n" + "\n".join(summaries),
                "confidence": 0.35,
                "risk_level": "MEDIUM",
                "structured": {"supervisor": True, "degraded": True, "worker_results": worker_results},
            }
            updated = merge_state(
                state,
                reasoning_result=reasoning_result,
                structured_output=reasoning_result["structured"],
                status=TaskStatus.REASONED.value,
                current_node="supervisor_merge",
                audit_log=append_audit(state, "supervisor_merge", "degraded", {"all_failed": True}),
            )
            get_state_store().save(updated)
            return updated

        merged = invoke_structured(
            "reasoning",
            REASONING_SYSTEM,
            _build_merge_context(state),
            trace_state=state,
        )
        worker_results = state.get("worker_results") or {}
        summaries = [
            f"[{item.get('domain')}] {item.get('summary', '')}"
            for item in worker_results.values()
            if item.get("summary")
        ]
        fallback_summary = "\n".join(summaries) if summaries else merged.get("summary", "")

        reasoning_result = {
            "summary": merged.get("summary") or fallback_summary,
            "confidence": float(merged.get("confidence", 0.75)),
            "risk_level": str(merged.get("risk_level", _max_risk(worker_results))).upper(),
            "structured": {
                "supervisor": True,
                "subtasks": state.get("subtasks"),
                "worker_results": worker_results,
                "merged": merged.get("structured", {}),
            },
        }

        max_risk = reasoning_result["risk_level"]
        review_required = max_risk in ("HIGH", "CRITICAL")

        updated = merge_state(
            state,
            reasoning_result=reasoning_result,
            structured_output=reasoning_result["structured"],
            review_required=review_required,
            status=TaskStatus.REASONED.value,
            current_node="supervisor_merge",
            audit_log=append_audit(
                state,
                "supervisor_merge",
                "success",
                {"confidence": reasoning_result["confidence"]},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"supervisor_merge: {exc}"],
            status=TaskStatus.FAILED.value,
            current_node="supervisor_merge",
            audit_log=append_audit(state, "supervisor_merge", "error", {"detail": str(exc)}),
        )


def _max_risk(worker_results: dict[str, Any]) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": 2}
    best = "LOW"
    for item in worker_results.values():
        risk = str(item.get("risk_level", "LOW")).upper()
        if order.get(risk, 0) > order.get(best, 0):
            best = risk
    return best
