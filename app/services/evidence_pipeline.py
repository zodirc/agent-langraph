"""Evidence operating system pipeline — wrapper around existing retrieval."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import EvidencePacket, RetrievalTrace
from app.runtime.state import AgentState
from app.services.evidence_arbitration import (
    apply_conflict_flags,
    detect_conflicts,
    detect_cross_source_conflicts,
    arbitration_summary,
)
from app.services.evidence_assembly import (
    apply_admission_gate,
    apply_token_budget,
    assemble_evidence_packets,
    classify_assembly_failures,
    deduplicate_candidates,
    detect_duplication_failures,
    detect_purpose_mismatch,
    enforce_source_diversity,
    hits_to_candidates,
    packets_to_hits,
)
from app.services.evidence_hierarchy import (
    collect_unified_evidence,
    hierarchy_summary,
    tool_evidence_from_state,
    user_evidence_from_state,
)
from app.services.failure_attribution import attribute_failures
from app.services.query_builder import build_query_object
from app.services.retrieval_decision import build_retrieval_decision
from app.services.retrieval_observability import (
    build_retrieval_trace,
    infer_recall_failures,
    log_retrieval_trace,
    observe_retrieval_trace,
    trace_to_audit,
)


def evidence_pipeline_enabled() -> bool:
    return bool(getattr(settings, "RETRIEVAL_ENABLE_EVIDENCE_PIPELINE", True))


def run_evidence_pipeline(
    state: AgentState,
    raw_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Process raw hybrid_search hits through the evidence OS layers.

    Returns state patch dict with retrieved_knowledge, evidence metadata, and trace.
    """
    existing = state.get("retrieval_decision")
    if isinstance(existing, dict) and existing.get("purpose"):
        from app.runtime.evidence_models import RetrievalDecision as _RD

        decision = _RD.model_validate(existing)
    else:
        decision = build_retrieval_decision(state)
    query = build_query_object(state, decision)

    if not decision.need_retrieval:
        trace = build_retrieval_trace(
            query=query,
            decision=decision,
            candidates=[],
            admitted=[],
            packets=[],
            filter_reasons={},
            failure_tags=[],
        )
        log_retrieval_trace(trace)
        return {
            "retrieval_decision": decision.model_dump(),
            "query_object": query.model_dump(),
            "evidence_packets": [],
            "retrieval_trace": trace.model_dump(),
            "retrieved_knowledge": [],
        }

    candidates = hits_to_candidates(raw_hits)
    pre_dedup = list(candidates)
    admitted, rejected, filter_reasons = apply_admission_gate(candidates, query, decision)
    if candidates and not admitted:
        filter_reasons["gate_all_filtered"] = filter_reasons.get("gate_all_filtered", 0) + 1

    admitted = deduplicate_candidates(admitted)
    admitted = enforce_source_diversity(admitted, decision)

    user_pkts = user_evidence_from_state(state)
    tool_pkts = tool_evidence_from_state(state)
    conflicts = detect_conflicts(admitted)
    conflicts.extend(detect_cross_source_conflicts(admitted, user_pkts, tool_pkts))
    packets = assemble_evidence_packets(admitted, query, decision)
    budget_tokens = int(getattr(settings, "RETRIEVAL_EVIDENCE_TOKEN_BUDGET", 0))
    packets = apply_token_budget(packets, max_tokens=budget_tokens or None)
    packets = apply_conflict_flags(packets, conflicts)

    hits = packets_to_hits(packets, admitted)
    failure_tags = infer_recall_failures(
        need_retrieval=decision.need_retrieval,
        raw_count=len(candidates),
        admitted_count=len(admitted),
        query=query,
    )
    failure_tags.extend(
        classify_assembly_failures(
            raw_count=len(candidates),
            admitted_count=len(admitted),
            packet_count=len(packets),
            filter_reasons=filter_reasons,
            decision=decision,
        )
    )
    if conflicts:
        failure_tags.append("evidence_conflict")
    failure_tags.extend(detect_duplication_failures(pre_dedup, admitted))
    failure_tags.extend(detect_purpose_mismatch(decision, admitted, query))
    failure_tags = list(dict.fromkeys(failure_tags))

    trace = build_retrieval_trace(
        query=query,
        decision=decision,
        candidates=candidates,
        admitted=admitted,
        packets=packets,
        filter_reasons=filter_reasons,
        failure_tags=failure_tags,
        conflicts=conflicts,
    )
    trace.debug_info["conflict_summary"] = arbitration_summary(conflicts)
    trace.debug_info["rejected_count"] = len(rejected)
    trace.debug_info["evidence_hierarchy"] = hierarchy_summary(collect_unified_evidence({**state, "evidence_packets": [p.model_dump() for p in packets]}))
    if state.get("task_drift"):
        trace.debug_info["task_drift"] = state["task_drift"]

    attribution = attribute_failures(retrieval_trace=trace.model_dump())
    log_retrieval_trace(trace)
    observe_retrieval_trace(trace)

    return {
        "retrieval_decision": decision.model_dump(),
        "query_object": query.model_dump(),
        "evidence_packets": [p.model_dump() for p in packets],
        "retrieval_trace": trace.model_dump(),
        "retrieved_knowledge": hits,
        "evidence_conflicts": [c.model_dump() for c in conflicts],
        "failure_attribution": attribution,
    }


def get_answer_mode(state: AgentState | dict[str, Any]) -> str:
    decision = state.get("retrieval_decision")
    if isinstance(decision, dict) and decision.get("answer_mode"):
        return str(decision["answer_mode"])
    return build_retrieval_decision(state).answer_mode


def get_evidence_packets(state: AgentState | dict[str, Any]) -> list[EvidencePacket]:
    raw = state.get("evidence_packets") or []
    return [EvidencePacket.model_validate(p) for p in raw if isinstance(p, dict)]


def pipeline_audit_extra(state_patch: dict[str, Any]) -> dict[str, Any]:
    trace_raw = state_patch.get("retrieval_trace")
    if isinstance(trace_raw, dict):
        return trace_to_audit(RetrievalTrace.model_validate(trace_raw))
    return {}
