"""输出节点

Output: compose final_answer + artifacts from reasoning/tools/RAG.
Then: output → END; memory/eval run async via close_turn_async; unfulfilled contracts stay PAUSED."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.rag_eval import merge_citations
from app.services.answer_compose import compose_user_answer
from app.services.artifact_tools import collect_file_artifacts
from app.services.audit_store import get_audit_store
from app.services.state_store import get_state_store


def _resolve_output_status(state: AgentState) -> str:
    """Pause (not complete) when a side-effect contract was left unfulfilled."""
    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
    )

    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state) and not is_turn_contract_fulfilled(
        state
    ):
        return TaskStatus.PAUSED.value

    if state.get("status") == TaskStatus.REJECTED.value:
        return TaskStatus.REJECTED.value
    if state.get("status") == TaskStatus.PAUSED.value:
        return TaskStatus.PAUSED.value
    return TaskStatus.COMPLETED.value


def output_node(state: AgentState) -> AgentState:
    """
    Compose final answer and structured output.

    Reads: reasoning_result, policy_result, tool_results, retrieved_knowledge
    Writes: final_answer, structured_output, artifacts, status, current_node, audit_log
    """
    try:
        if state.get("status") == TaskStatus.REJECTED.value:
            answer = "Task rejected by policy or human review."
            structured: dict[str, Any] = {"rejected": True, "policy_result": state.get("policy_result")}
        else:
            reasoning = state.get("reasoning_result") or {}
            structured_body = reasoning.get("structured") if isinstance(
                reasoning.get("structured"), dict
            ) else {}
            answer = compose_user_answer(
                str(reasoning.get("summary") or ""),
                structured_body,
            ) or "No reasoning summary available."
            retrieved = state.get("retrieved_knowledge") or []
            if settings.RAG_CITATION_ENABLED and retrieved and "[" not in answer:
                refs = " ".join(
                    f"[{d.get('doc_id')}]"
                    for d in retrieved[:3]
                    if d.get("doc_id")
                )
                if refs:
                    answer = f"{answer}\n\nSources: {refs}"
            file_artifacts = collect_file_artifacts(state.get("tool_results"))
            total_file_bytes = sum(int(item.get("bytes") or 0) for item in file_artifacts)
            total_chunks = sum(int(item.get("chunks_written") or 1) for item in file_artifacts)
            structured = {
                "policy_result": state.get("policy_result"),
                "confidence": reasoning.get("confidence"),
                "plan": state.get("plan"),
                "knowledge_count": len(state.get("retrieved_knowledge") or []),
                "tool_count": len(state.get("tool_results") or []),
                "memory_count": len(state.get("memory_hits") or []),
                "structured_reasoning": reasoning.get("structured", {}),
                "longform_mode": bool((state.get("input_payload") or {}).get("longform_mode")),
                "chapter_index": (state.get("input_payload") or {}).get("chapter_index"),
                "artifact_bytes": total_file_bytes,
                "artifact_chunks": total_chunks,
                "citations": merge_citations(answer, retrieved),
            }

        from app.services.turn_contract import (
            contract_requires_side_effects,
            is_turn_contract_fulfilled,
        )
        from app.services.turn_contract_lifecycle import (
            REASON_INCONSISTENT,
            invalidate_turn_contract_payload,
        )

        payload_out = dict(state.get("input_payload") or {})
        status_override: Optional[str] = None
        if contract_requires_side_effects(payload_out, state=state) and not is_turn_contract_fulfilled(
            state
        ):
            payload_out = invalidate_turn_contract_payload(payload_out, REASON_INCONSISTENT)
            status_override = TaskStatus.PAUSED.value

        file_artifacts = collect_file_artifacts(state.get("tool_results"))
        artifacts: list[dict[str, Any]] = [
            {
                "type": "audit_summary",
                "entries": len(state.get("audit_log", [])),
            },
            *file_artifacts,
        ]

        resolved_status = status_override or _resolve_output_status(state)
        updated = merge_state(
            state,
            input_payload=payload_out,
            final_answer=answer,
            structured_output=structured,
            artifacts=artifacts,
            status=resolved_status,
            current_node="output",
            audit_log=append_audit(
                state,
                "output",
                "success",
                {
                    "answer_length": len(answer),
                    "artifact_count": len(file_artifacts),
                    "memory_count": len(state.get("memory_hits") or []),
                },
            ),
        )
        get_audit_store().append_events(state["task_id"], updated.get("audit_log", []))
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"output: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="output",
            audit_log=append_audit(state, "output", "error", {"detail": str(exc)}),
        )
