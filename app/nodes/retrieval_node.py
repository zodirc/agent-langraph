from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.knowledge_store import get_knowledge_store
from app.services.reasoning_trace import report_boundary, report_retrieval_trace
from app.services.memory_store import get_memory_store
from app.services.retrieval_policy import (
    restrict_memory_to_current_session,
    should_skip_session_memory_retrieval,
    skip_knowledge_retrieval,
)
from app.services.state_store import get_state_store


def retrieval_node(state: AgentState) -> AgentState:
    """
    Retrieve knowledge and historical memory hits.

    Reads: input_payload, plan
    Writes: retrieved_knowledge, memory_hits, status, current_node, audit_log
    """
    try:
        payload = state.get("input_payload", {})
        from app.services.memory_query import build_memory_search_query

        query = build_memory_search_query(state)
        report_boundary("retrieval", "enter", query[:80])
        if skip_knowledge_retrieval(state):
            knowledge: list[dict] = []
        else:
            knowledge = get_knowledge_store().hybrid_search(query)
        from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch

        knowledge = sanitize_retrieved_batch(knowledge)
        if should_skip_session_memory_retrieval(state):
            memories: list[dict] = []
        else:
            memories = get_memory_store().search_for_context(
                query,
                user_id=state.get("user_id", "anonymous"),
                task_id=state["task_id"],
                session_id=state.get("session_id") or state["task_id"],
                restrict_to_session=restrict_memory_to_current_session(state),
            )
        from app.services.code_artifact_pipeline import filter_memory_hits

        memories = filter_memory_hits(state, memories)
        from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch

        memories = sanitize_retrieved_batch(memories)
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_session_memory_retrieval(hit=bool(memories))

        updated = merge_state(
            state,
            retrieved_knowledge=knowledge,
            memory_hits=memories,
            status=TaskStatus.RETRIEVED.value,
            current_node="retrieval",
            audit_log=append_audit(
                state,
                "retrieval",
                "success",
                {"knowledge_count": len(knowledge), "memory_count": len(memories)},
            ),
        )
        from app.services.turn_event_log import record_turn_event

        updated = record_turn_event(
            updated,
            "artifact_retrieved",
            "knowledge",
            "retrieval",
            {"knowledge_count": len(knowledge), "memory_count": len(memories), "sanitized": True},
        )
        report_retrieval_trace(updated)
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"retrieval: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="retrieval",
            audit_log=append_audit(state, "retrieval", "error", {"detail": str(exc)}),
        )
