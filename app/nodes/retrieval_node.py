"""检索节点 retrieval_node：hybrid_search 与会话记忆写入 state。

路由 route_after_retrieval → tool、writing 或 reasoning。

Retrieval node: hybrid_search and session memory; route_after_retrieval next hop.
"""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.knowledge_store import get_knowledge_store
from app.services.reasoning_trace import report_boundary
from app.services.memory_store import get_memory_store
from app.services.retrieval_policy import (
    retrieval_domains_for_state,
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
        from app.services.memory_query import build_memory_search_query
        from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch

        from app.services.evidence_pipeline import (
            evidence_pipeline_enabled,
            pipeline_audit_extra,
            run_evidence_pipeline,
        )
        from app.services.query_builder import (
            build_query_object,
            expand_multi_queries,
            rewrite_query_with_llm,
        )
        from app.services.retrieval_decision import build_retrieval_decision

        decision = build_retrieval_decision(state)
        query_obj = build_query_object(state, decision)
        query = query_obj.standalone_query or build_memory_search_query(state)
        domains = retrieval_domains_for_state(state)
        report_boundary("retrieval", "enter", query[:80])

        pipeline_patch: dict = {}
        if skip_knowledge_retrieval(state):
            knowledge: list[dict] = []
            if evidence_pipeline_enabled():
                pipeline_patch = run_evidence_pipeline(state, [])
        else:
            search_queries = expand_multi_queries(query_obj)
            raw_hits: list[dict] = []
            for sq in search_queries:
                batch = sanitize_retrieved_batch(
                    get_knowledge_store().hybrid_search(
                        sq,
                        domains=domains,
                        query_object=query_obj,
                        retrieval_decision=decision,
                    )
                )
                seen = {h.get("doc_id") for h in raw_hits}
                for hit in batch:
                    if hit.get("doc_id") not in seen:
                        raw_hits.append(hit)
                        seen.add(hit.get("doc_id"))
            if evidence_pipeline_enabled():
                pipeline_patch = run_evidence_pipeline(state, raw_hits)
                trace = pipeline_patch.get("retrieval_trace") or {}
                if "gate_all_filtered" in (trace.get("failure_tags") or []):
                    max_retries = int(getattr(settings, "RETRIEVAL_QUERY_REWRITE_MAX_RETRIES", 1))
                    retry_query: str | None = None
                    if max_retries > 0 and getattr(settings, "RETRIEVAL_QUERY_REWRITE_LLM", False):
                        retry_query = rewrite_query_with_llm(query, state)
                    if not retry_query and query_obj.must_have_terms:
                        retry_query = f"{query} {' '.join(query_obj.must_have_terms[:6])}".strip()
                    if retry_query:
                        retry_hits = sanitize_retrieved_batch(
                            get_knowledge_store().hybrid_search(
                                retry_query,
                                domains=domains,
                                query_object=query_obj,
                                retrieval_decision=decision,
                            )
                        )
                        pipeline_patch = run_evidence_pipeline(state, retry_hits)
                        rt = pipeline_patch.get("retrieval_trace")
                        if isinstance(rt, dict):
                            rt["query_retry"] = True
                            rt["query_retry_text"] = retry_query[:200]
                knowledge = pipeline_patch.get("retrieved_knowledge") or raw_hits
            else:
                knowledge = raw_hits
        knowledge = sanitize_retrieved_batch(knowledge)
        from app.services.relevance_gate import gate_stats

        _observe_retrieval_noise(gate_stats(knowledge))
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

        memories = sanitize_retrieved_batch(filter_memory_hits(state, memories))
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_session_memory_retrieval(hit=bool(memories))

        audit_detail: dict = {
            "knowledge_count": len(knowledge),
            "memory_count": len(memories),
        }
        if pipeline_patch:
            audit_detail.update(pipeline_audit_extra(pipeline_patch))

        merge_kwargs: dict = {
            "retrieved_knowledge": knowledge,
            "memory_hits": memories,
            "status": TaskStatus.RETRIEVED.value,
            "current_node": "retrieval",
            "audit_log": append_audit(state, "retrieval", "success", audit_detail),
        }
        if pipeline_patch:
            for key in (
                "retrieval_decision",
                "query_object",
                "evidence_packets",
                "retrieval_trace",
                "evidence_conflicts",
                "failure_attribution",
            ):
                if key in pipeline_patch:
                    merge_kwargs[key] = pipeline_patch[key]

        updated = merge_state(state, **merge_kwargs)
        from app.services.turn_event_log import record_turn_event

        updated = record_turn_event(
            updated,
            "artifact_retrieved",
            "knowledge",
            "retrieval",
            {"knowledge_count": len(knowledge), "memory_count": len(memories), "sanitized": True},
        )
        from app.services.context_registry import persist_retrieval_context

        updated = persist_retrieval_context(updated, knowledge=knowledge, memories=memories)
        # 检索 trace 由 graph_runner.trace_after_node(retrieval) 统一发出，避免重复
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


def _observe_retrieval_noise(stats: dict) -> None:
    if not stats:
        return
    try:
        from app.config.settings import settings

        if not settings.METRICS_ENABLED:
            return
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().observe_rag_retrieval_noise(stats)
    except Exception:
        pass
