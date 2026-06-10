"""
Unified Context Governance entry (ADR §7, §9.2).

All LLM calls that assemble session/history context must pass through
prepare_governed_payload() or build_context_envelope().
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.context_assembler import assemble_context_envelope
from app.services.context_collectors import (
    collect_code_agent_context_items,
    collect_diagnostic_context_items,
    collect_writing_context_items,
)
from app.services.context_items import (
    ContextEnvelope,
    ContextItem,
    ContextPurpose,
    new_context_id,
)
from app.services.context_policy import get_prompt_context_policy, scale_policy_for_budget

# Purposes routed through llm_client governance hook when trace_state is present.
GOVERNED_LLM_PURPOSES: frozenset[str] = frozenset(
    {
        "planning",
        "reasoning",
        "writing",
        "reviewing",
        "reflection",
        "routing",
        "summarization",
        "code_agent",
        "session_turn",
        "intent_observation",
    }
)

# Purposes that never receive session governance (eval-only, no AgentState).
GOVERNANCE_EXEMPT_PURPOSES: frozenset[str] = frozenset({"rag_eval"})

from app.services.conversation_context import (
    build_session_outcomes_digest,
    conversation_history_for_llm,
    conversation_history_from_state,
)
from app.services.working_memory import (
    semantic_summary_item_from_state,
    working_memory_from_state,
)


def context_governance_enabled() -> bool:
    return bool(getattr(settings, "CONTEXT_GOVERNANCE_ENABLED", False))


def should_govern_llm_purpose(
    purpose: str,
    trace_state: dict[str, Any] | None = None,
) -> bool:
    if not context_governance_enabled() or purpose in GOVERNANCE_EXEMPT_PURPOSES:
        return False
    if purpose in GOVERNED_LLM_PURPOSES:
        return True
    if isinstance(trace_state, dict) and trace_state.get("task_id"):
        return True
    return False


def _resolve_purpose(state: dict[str, Any], purpose: str) -> ContextPurpose:
    from app.services.context_policy import resolve_purpose_for_state

    resolved = resolve_purpose_for_state(state, purpose)
    if resolved in GOVERNED_LLM_PURPOSES:
        return resolved  # type: ignore[return-value]
    return purpose if purpose in GOVERNED_LLM_PURPOSES else "reasoning"  # type: ignore[return-value]


def collect_context_items(
    state: AgentState | dict[str, Any],
    *,
    purpose: ContextPurpose,
) -> list[ContextItem]:
    """Collect raw context from session/state into ContextItem list."""
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    items: list[ContextItem] = []

    goal = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()
    if goal:
        items.append(
            ContextItem(
                id=new_context_id("turn"),
                kind="user_turn",
                source="session",
                role="user",
                content=goal,
                priority="critical",
                compressible=False,
                droppable=False,
                bucket="current_turn",
            )
        )

    raw_history = conversation_history_from_state(state)
    llm_history = conversation_history_for_llm(raw_history)
    for msg in llm_history:
        if goal and str(msg.get("content", "")).strip() == goal:
            continue
        items.append(
            ContextItem.from_message(
                msg,
                kind="recent_history",
                source="session",
                priority="medium",
                compressible=True,
                droppable=False,
            )
        )

    if summary_item := semantic_summary_item_from_state(state, raw_history):
        if purpose in (
            "planning",
            "reasoning",
            "routing",
            "reflection",
            "writing",
            "reviewing",
        ):
            items.append(summary_item)

    items.append(working_memory_from_state(state).to_context_item())

    if purpose == "intent_observation":
        digest = build_session_outcomes_digest(state)
        if digest:
            items.append(
                ContextItem(
                    id=new_context_id("out"),
                    kind="working_memory",
                    source="session",
                    role="system",
                    content=digest,
                    priority="low",
                    compressible=True,
                    droppable=True,
                    bucket="working_memory",
                )
            )

    from app.services.context_fingerprint import registry_fingerprints, strict_dedupe_key

    registry_fps = registry_fingerprints(state)

    for hit in (state.get("memory_hits") or [])[:8]:
        if getattr(settings, "RETRIEVAL_UNIFIED_HIERARCHY", True):
            continue
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("summary") or hit.get("content") or hit)[:2000]
        candidate = ContextItem(
            id=new_context_id("mem"),
            kind="episodic_memory",
            source="memory",
            role="system",
            content=text,
            priority="medium",
            compressible=True,
            droppable=True,
            bucket="retrieved_memory",
            meta={"memory_id": hit.get("id"), "score": hit.get("score")},
        )
        if strict_dedupe_key(candidate) not in registry_fps:
            items.append(candidate)

    from app.services.relevance_gate import (
        extract_evidence_snippet,
        purpose_max_knowledge,
        should_inject_knowledge,
    )

    query = goal or str(
        (state.get("input_payload") or {}).get("query")
        or (state.get("input_payload") or {}).get("question")
        or ""
    ).strip()
    query_obj = state.get("query_object") if isinstance(state.get("query_object"), dict) else {}
    if query_obj.get("standalone_query"):
        query = str(query_obj["standalone_query"])

    max_knowledge = purpose_max_knowledge(purpose)
    injected_knowledge = 0

    if max_knowledge > 0:
        from app.services.evidence_hierarchy import collect_unified_evidence

        hierarchy_types = {"user_input", "tool_result", "memory", "user_upload"}
        if getattr(settings, "RETRIEVAL_UNIFIED_HIERARCHY", True):
            for pkt in collect_unified_evidence(state):
                if pkt.source_type not in hierarchy_types:
                    continue
                snippet = str(pkt.snippet_text or "").strip()
                if not snippet:
                    continue
                candidate = ContextItem(
                    id=new_context_id("evp"),
                    kind="knowledge" if pkt.source_type != "memory" else "episodic_memory",
                    source="retrieval" if pkt.source_type != "memory" else "memory",
                    role="system",
                    content=snippet[:3000],
                    priority="high" if pkt.source_type in ("user_input", "tool_result") else "medium",
                    compressible=True,
                    droppable=pkt.source_type == "memory",
                    bucket="retrieved_knowledge" if pkt.source_type != "memory" else "retrieved_memory",
                    meta={
                        "doc_id": pkt.chunk_id,
                        "evidence_packet_id": pkt.packet_id,
                        "support_type": pkt.support_type,
                        "authority_level": pkt.authority_level,
                        "source_type": pkt.source_type,
                    },
                )
                if strict_dedupe_key(candidate) not in registry_fps:
                    items.append(candidate)

        pipeline_packets = state.get("evidence_packets") or []
        used_packets = False
        if pipeline_packets and getattr(settings, "RETRIEVAL_ENABLE_SNIPPET_FIRST", True):
            used_packets = True
            for pkt in pipeline_packets[:max_knowledge]:
                if not isinstance(pkt, dict):
                    continue
                snippet = str(pkt.get("snippet_text") or "").strip()
                if not snippet:
                    continue
                title = str(pkt.get("source_title") or "")
                text = f"[{title}] {snippet}".strip() if title else snippet
                candidate = ContextItem(
                    id=new_context_id("evp"),
                    kind="knowledge",
                    source="retrieval",
                    role="system",
                    content=text[:3000],
                    priority="medium",
                    compressible=True,
                    droppable=False,
                    bucket="retrieved_knowledge",
                    meta={
                        "doc_id": pkt.get("chunk_id"),
                        "evidence_packet_id": pkt.get("packet_id"),
                        "support_type": pkt.get("support_type"),
                        "authority_level": pkt.get("authority_level"),
                        "has_conflict": pkt.get("has_conflict"),
                    },
                )
                if strict_dedupe_key(candidate) not in registry_fps:
                    items.append(candidate)
                    injected_knowledge += 1

        if used_packets:
            doc_iter: list = []
        else:
            doc_iter = (state.get("retrieved_knowledge") or [])[:12]

        for doc in doc_iter:
            if injected_knowledge >= max_knowledge:
                break
            if not isinstance(doc, dict):
                continue
            inject, priority, droppable = should_inject_knowledge(doc, purpose=purpose)
            if not inject:
                continue
            raw_text = str(doc.get("content") or doc.get("text") or "")
            if not raw_text:
                continue
            title = str(doc.get("title") or "")
            text = extract_evidence_snippet(raw_text, query, title=title)[:3000]
            candidate = ContextItem(
                id=new_context_id("doc"),
                kind="knowledge",
                source="retrieval",
                role="system",
                content=text,
                priority=priority,  # type: ignore[arg-type]
                compressible=True,
                droppable=droppable,
                bucket="retrieved_knowledge",
                meta={
                    "doc_id": doc.get("doc_id") or doc.get("id"),
                    "source": doc.get("source"),
                    "relevance_score": doc.get("relevance_score"),
                    "relevance_passed": doc.get("relevance_passed"),
                    "retrieval_stage": doc.get("retrieval_stage"),
                },
            )
            if strict_dedupe_key(candidate) not in registry_fps:
                items.append(candidate)
                injected_knowledge += 1

    for tool in (state.get("tool_results") or [])[:10]:
        if not isinstance(tool, dict):
            continue
        name = tool.get("tool") or tool.get("name") or "tool"
        status = tool.get("status", "?")
        result = tool.get("result")
        snippet = str(result)[:1200] if result is not None else ""
        candidate = ContextItem(
            id=new_context_id("tool"),
            kind="tool_output",
            source="tool",
            role="tool",
            content=f"[{name}] {status}: {snippet}",
            priority=(
                "high"
                if purpose in ("reasoning", "writing", "reviewing")
                else "low"
            ),
            compressible=True,
            droppable=purpose in ("planning", "routing"),
            bucket="tool_observations",
            meta={"tool": name, "status": status},
        )
        if strict_dedupe_key(candidate) not in registry_fps:
            items.append(candidate)

    from app.services.context_registry import registry_items_for_collection

    registry = registry_items_for_collection(state)
    seen_keys = {strict_dedupe_key(i) for i in items}
    for reg_item in registry:
        key = strict_dedupe_key(reg_item)
        if key not in seen_keys:
            items.append(reg_item)
            seen_keys.add(key)

    items.extend(collect_writing_context_items(state, purpose=purpose))
    items.extend(collect_code_agent_context_items(state))
    items.extend(collect_diagnostic_context_items(state))

    if purpose == "code_agent" or _resolve_purpose(state, purpose) == "code_agent":
        from app.services.context_file_slice import expand_file_context_item

        task_id = str(state.get("task_id") or "")
        expanded: list[ContextItem] = []
        for item in items:
            if item.kind == "file_slice" and item.meta.get("truncated"):
                ex = expand_file_context_item(item, task_id=task_id)
                if ex:
                    expanded.append(ex)
        items.extend(expanded)

    return items


def build_context_envelope(
    state: AgentState | dict[str, Any],
    *,
    purpose: ContextPurpose,
    model_name: str = "",
    token_budget_total: int | None = None,
) -> ContextEnvelope:
    purpose = _resolve_purpose(state, purpose)  # type: ignore[assignment]
    policy = get_prompt_context_policy(purpose)
    budget = token_budget_total
    if budget is None:
        from app.services.resource_budget import resolve_prompt_token_budget

        budget = resolve_prompt_token_budget(
            state,
            policy_default=policy.default_token_budget,
            purpose=purpose,
        )
        if budget <= 0:
            budget = int(
                getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 0)
                or policy.default_token_budget
            )
    policy = scale_policy_for_budget(policy, budget)
    items = collect_context_items(state, purpose=purpose)
    t0 = time.perf_counter()
    envelope = assemble_context_envelope(
        items,
        purpose=purpose,
        policy=policy,
        token_budget_total=budget,
        model_name=model_name,
        state=state,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    envelope.trace["assembly_latency_ms"] = round(elapsed_ms, 2)
    _record_governance_metrics(envelope, elapsed_ms=elapsed_ms)
    return envelope


def file_context_bundle_from_envelope(envelope: ContextEnvelope) -> dict[str, Any]:
    """Structured file/diagnostic slices for writing JSON payloads."""
    bundle: dict[str, list[dict[str, Any]]] = {
        "file_slices": [],
        "diagnostics": [],
        "workspace": [],
    }
    for item in envelope.items_kept:
        if item.kind in ("file_slice", "symbol_slice", "git_diff", "workspace_summary"):
            bundle["file_slices"].append(item.to_dict())
        elif item.kind in ("diagnostic", "test_failure", "terminal_output"):
            bundle["diagnostics"].append(item.to_dict())
    return bundle


def prepare_governed_payload(
    state: AgentState | dict[str, Any],
    purpose: str,
    payload: dict[str, Any],
    *,
    token_budget_total: int | None = None,
) -> tuple[dict[str, Any], ContextEnvelope]:
    """
    Merge governed context into a JSON user payload.

    Replaces conversation_history when present; injects context_governance trace
    and governed_file_context for writing/reviewing.
    """
    p = _resolve_purpose(state, purpose)
    envelope = build_context_envelope(
        state, purpose=p, token_budget_total=token_budget_total
    )
    out = dict(payload)
    transcript = envelope.conversation_history_for_payload()
    if "conversation_history" in out or p in (
        "planning",
        "reasoning",
        "writing",
        "reviewing",
        "reflection",
        "code_agent",
        "session_turn",
        "intent_observation",
    ):
        out["conversation_history"] = transcript
    if "conversation" in out:
        out["conversation"] = transcript or out.get("conversation")
    if p in ("writing", "reviewing", "code_agent"):
        out["governed_file_context"] = file_context_bundle_from_envelope(envelope)
    out["context_governance"] = envelope.to_debug_dict()
    return out, envelope


def prepare_governed_user_json(
    state: AgentState | dict[str, Any],
    purpose: str,
    payload: dict[str, Any],
    *,
    token_budget_total: int | None = None,
) -> str:
    governed, _ = prepare_governed_payload(
        state, purpose, payload, token_budget_total=token_budget_total
    )
    return json.dumps(governed, ensure_ascii=False)


def apply_governance_to_user_content(
    purpose: str,
    user_content: str,
    trace_state: dict[str, Any] | None,
) -> str:
    """
    Central hook for llm_client: govern JSON user blobs when trace_state is AgentState.
    """
    if not trace_state or not should_govern_llm_purpose(purpose, trace_state):
        return user_content
    try:
        payload = json.loads(user_content)
    except json.JSONDecodeError:
        return user_content
    if not isinstance(payload, dict):
        return user_content
    if payload.get("context_governance"):
        return user_content
    governed, envelope = prepare_governed_payload(trace_state, purpose, payload)
    mutate_state_context_trace(trace_state, envelope)
    return json.dumps(governed, ensure_ascii=False)


def _append_envelope_trace(
    trace_ctx: dict[str, Any],
    envelope: ContextEnvelope,
) -> dict[str, Any]:
    traces = list(trace_ctx.get("context_envelopes") or [])
    traces.append(
        {
            "purpose": envelope.purpose,
            "kept": len(envelope.items_kept),
            "dropped": len(envelope.items_dropped),
            "compressed": len(envelope.items_compressed),
            "budget": envelope.token_budget_total,
        }
    )
    trace_ctx["context_envelopes"] = traces[-12:]
    trace_ctx["last_context_composition"] = envelope.trace.get("composition_view")
    return trace_ctx


def mutate_state_context_trace(
    state: dict[str, Any],
    envelope: ContextEnvelope,
) -> None:
    """In-place update trace_context on the state dict used as trace_state."""
    trace_ctx = _append_envelope_trace(dict(state.get("trace_context") or {}), envelope)
    state["trace_context"] = trace_ctx


def _record_governance_metrics(
    envelope: ContextEnvelope,
    *,
    elapsed_ms: float = 0.0,
) -> None:
    try:
        from app.services.metrics_service import get_metrics_service

        metrics = get_metrics_service()
        purpose = envelope.purpose
        for alloc in envelope.bucket_allocations:
            if alloc.final_tokens > 0:
                metrics.observe_context_bucket_tokens(
                    purpose, alloc.bucket, alloc.final_tokens
                )
        for item in envelope.items_dropped:
            reason = "dropped"
            for action in envelope.trace.get("actions") or []:
                if action.get("item_id") == item.id:
                    reason = str(action.get("reason") or reason)
                    break
            metrics.inc_context_drop(purpose, item.resolve_bucket(), reason)
        for item in envelope.items_compressed:
            metrics.inc_context_compress(
                purpose,
                item.resolve_bucket(),
                "governance",
            )
        if elapsed_ms > 0:
            metrics.observe_context_assembly_latency_ms(purpose, elapsed_ms)
    except Exception:
        pass


def governed_conversation_history(
    state: AgentState | dict[str, Any],
    *,
    purpose: ContextPurpose = "planning",
) -> list[dict[str, Any]]:
    envelope = build_context_envelope(state, purpose=purpose)
    mutate_state_context_trace(state, envelope) if isinstance(state, dict) else None
    return envelope.conversation_history_for_payload()


def governed_reasoning_context(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    from app.services.fact_layer import build_turn_facts
    from app.services.runtime_capabilities import (
        build_runtime_capabilities,
        reasoning_instructions_for_state,
    )

    governed, envelope = prepare_governed_payload(
        state,
        "reasoning",
        {
            "goal": (state.get("input_payload") or {}).get("goal"),
            "session_turn": state.get("session_turn"),
            "conversation_history": [],
            "session_outcomes_digest": build_session_outcomes_digest(state),
            "turn_facts": state.get("turn_facts") or build_turn_facts(state),
            "retrieved_knowledge": state.get("retrieved_knowledge") or [],
            "memory_hits": (state.get("memory_hits") or [])[:5],
            "runtime_capabilities": build_runtime_capabilities(),
            "writing_intent": (state.get("input_payload") or {}).get("writing_intent"),
            "mission": state.get("mission") or (state.get("input_payload") or {}).get("mission"),
            "instructions": reasoning_instructions_for_state(state),
        },
    )
    if isinstance(state, dict):
        mutate_state_context_trace(state, envelope)
    return governed


def resolve_context_panel_meta(
    state: AgentState | dict[str, Any],
    *,
    purpose: str = "reasoning",
    model_id: str | None = None,
) -> dict[str, Any]:
    """Runtime model + session metering for Web context panel (Copilot/Cursor-style)."""
    from app.services.context_meter import build_session_meter

    raw = state if isinstance(state, dict) else dict(state)
    meter = build_session_meter(raw, purpose=purpose, model_id=model_id)
    return {
        "data_mode": meter.get("data_mode"),
        "model_name": meter.get("model_name"),
        "model_id": meter.get("model_id"),
        "billing_source": meter.get("billing_source"),
        "session_tokens_used": meter.get("session_tokens_consumed"),
        "session_tokens_consumed": meter.get("session_tokens_consumed"),
        "session_tokens_consumed_billed": meter.get("session_tokens_consumed_billed"),
        "session_tokens_consumed_local": meter.get("session_tokens_consumed_local"),
        "session_prompt_tokens_consumed": meter.get("session_prompt_tokens_consumed"),
        "session_completion_tokens_consumed": meter.get("session_completion_tokens_consumed"),
        "session_prompt_tokens_consumed_billed": meter.get("session_prompt_tokens_consumed_billed"),
        "session_completion_tokens_consumed_billed": meter.get("session_completion_tokens_consumed_billed"),
        "session_prompt_tokens_consumed_local": meter.get("session_prompt_tokens_consumed_local"),
        "session_completion_tokens_consumed_local": meter.get("session_completion_tokens_consumed_local"),
        "last_request_tokens": meter.get("last_request_tokens"),
        "llm_call_count": meter.get("llm_call_count"),
        "last_prompt_tokens": meter.get("last_prompt_tokens"),
        "last_completion_tokens": meter.get("last_completion_tokens"),
        "last_provider_prompt_tokens": meter.get("last_provider_prompt_tokens"),
        "last_provider_completion_tokens": meter.get("last_provider_completion_tokens"),
        "last_local_prompt_tokens": meter.get("last_local_prompt_tokens"),
        "last_local_completion_tokens": meter.get("last_local_completion_tokens"),
        "last_call_purpose": meter.get("last_call_purpose"),
        "model_context_window_tokens": meter.get("model_context_window_tokens"),
        "context_length_used_tokens": meter.get("context_length_used_tokens"),
        "context_length_max_tokens": meter.get("context_length_max_tokens"),
        "context_length_used_percent": meter.get("context_length_used_percent"),
        "context_length_available_tokens": meter.get("context_length_available_tokens"),
        "context_length_source": meter.get("context_length_source"),
        "context_window_tokens": meter.get("context_window_tokens"),
        "session_context_window_tokens": meter.get("session_context_window_tokens"),
        "context_window_used_tokens": meter.get("context_window_used_tokens"),
        "context_window_available_tokens": meter.get("context_window_available_tokens"),
        "context_window_used_percent": meter.get("context_window_used_percent"),
    }


def _enrich_composition_view(
    composition: dict[str, Any],
    *,
    envelope: ContextEnvelope,
    panel_meta: dict[str, Any],
) -> dict[str, Any]:
    out = dict(composition)
    assembly_tokens = sum(int(a.final_tokens) for a in envelope.bucket_allocations)
    model = str(panel_meta.get("model_name") or envelope.model_name or "")
    out["model_name"] = model
    out["token_budget_total"] = envelope.token_budget_total
    out["assembly_tokens"] = assembly_tokens
    out["session_tokens_used"] = int(panel_meta.get("session_tokens_used") or 0)
    out["session_token_limit"] = int(panel_meta.get("session_token_limit") or 0)
    trace = dict(out.get("trace") or {})
    trace.update(
        {
            "model_name": model,
            "token_budget_total": envelope.token_budget_total,
            "assembly_tokens": assembly_tokens,
            "session_tokens_used": out["session_tokens_used"],
            "session_token_limit": out["session_token_limit"],
        }
    )
    out["trace"] = trace
    return out


def build_prompt_composition_for_state(
    state: AgentState | dict[str, Any],
    *,
    purpose: ContextPurpose = "reasoning",
    model_id: str | None = None,
) -> dict[str, Any]:
    """On-demand prompt composition view (ADR §11.3)."""
    panel_meta = resolve_context_panel_meta(
        state, purpose=purpose, model_id=model_id
    )
    envelope = build_context_envelope(
        state,
        purpose=purpose,
        model_name=str(panel_meta.get("model_name") or ""),
    )
    raw = envelope.trace.get("composition_view") or envelope.to_debug_dict()
    if not isinstance(raw, dict):
        raw = {}
    return _enrich_composition_view(raw, envelope=envelope, panel_meta=panel_meta)


def attach_context_trace_to_state(
    state: AgentState,
    envelope: ContextEnvelope,
) -> AgentState:
    from app.runtime.state import merge_state

    trace_ctx = _append_envelope_trace(dict(state.get("trace_context") or {}), envelope)
    return merge_state(state, trace_context=trace_ctx)


def manual_compress_context(
    state: AgentState,
    *,
    scope: str = "transcript",
    token_budget: int | None = None,
) -> tuple[AgentState, ContextEnvelope]:
    """
    Policy-constrained manual compress (ADR §1.1 #6, §5.6).

    scope: transcript | all_compressible | aggressive
    """
    from app.runtime.state import merge_state
    from app.services.conversation_context import compress_session_history

    allowed = {"transcript", "all_compressible", "aggressive"}
    if scope not in allowed:
        scope = "transcript"

    working = dict(state)
    if scope in ("transcript", "all_compressible", "aggressive"):
        history = conversation_history_from_state(working)
        compressed = compress_session_history(history, state=working)
        payload = dict(working.get("input_payload") or {})
        payload["conversation_history"] = compressed
        working = merge_state(working, conversation_history=compressed, input_payload=payload)  # type: ignore[arg-type]

    purpose: ContextPurpose = "summarization" if scope == "aggressive" else "reasoning"  # type: ignore[assignment]
    budget = token_budget
    if budget is None:
        budget = max(4096, int(getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 12000) // 2))
    if scope == "aggressive":
        budget = max(2048, budget // 2)

    envelope = build_context_envelope(working, purpose=purpose, token_budget_total=budget)
    trace_ctx = dict(working.get("trace_context") or {})
    trace_ctx["manual_compress"] = {
        "scope": scope,
        "token_budget": budget,
        "at": time.time(),
    }
    working["trace_context"] = trace_ctx
    updated = attach_context_trace_to_state(working, envelope)  # type: ignore[arg-type]
    return updated, envelope


def transcript_for_llm_payload(
    state: AgentState | dict[str, Any],
    *,
    purpose: str = "planning",
) -> list[dict[str, Any]]:
    """ADR §13.1: transcript for JSON only via governance envelope."""
    if not context_governance_enabled():
        return conversation_history_for_llm(conversation_history_from_state(state))
    return build_context_envelope(state, purpose=purpose).conversation_history_for_payload()  # type: ignore[arg-type]
