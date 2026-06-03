"""推理节点
Route: reflection (low confidence

Reasoning: structured answer from context; fast_reasoning shortcut optional.
route_audit) | policy | dead_letter.
Skill: reasoning_overlay in system prompt."""

from __future__ import annotations

import json

from app.config.prompts import build_reasoning_system_prompt, resolve_reasoning_mode
from app.services.resource_budget import (
    BudgetExceededError,
    budget_context_from_state,
    init_task_budget,
)
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.fast_reasoning import try_fast_reasoning
from app.services.fact_layer import (
    apply_reasoning_guard,
    attach_turn_facts,
    build_turn_facts,
)
from app.services.observation import (
    attach_observation,
    build_observation,
    reasoning_context_from_observation,
)
from app.services.llm_client import (
    extract_json_with_repair,
    invoke_structured,
    stream_structured,
)
from app.services.reasoning_trace import (
    answer_stream_enabled,
    emit_final_artifact_fences,
    extract_field_text,
    report_boundary,
    report_reasoning_context,
    report_status_trace,
    stream_llm_trace,
    trace_enabled,
)
from app.services.state_store import get_state_store
from app.services.metrics_service import get_metrics_service
from app.services.reasoning_shortcut import (
    should_use_execution_summary,
    summary_from_turn_execution,
)


def reasoning_node(state: AgentState) -> AgentState:
    """
    Synthesize turn_facts (ground truth), knowledge, and memory into reasoning result.

    Reads: turn_facts, retrieved_knowledge, memory_hits, input_payload
    Writes: reasoning_result, status, current_node, audit_log
    """
    try:
        if state.get("token_budget") is None:
            state = init_task_budget(state)
        budget_ctx = budget_context_from_state(state)
        mode = resolve_reasoning_mode(state)
        reasoning_system = build_reasoning_system_prompt(mode, state=state)
        state = attach_observation(state) if state.get("mission") else attach_turn_facts(state)
        payload = state.get("input_payload", {})
        turn_facts = (
            state.get("observation")
            or state.get("turn_facts")
            or build_observation(state)
            if state.get("mission")
            else build_turn_facts(state)
        )

        if should_use_execution_summary(state, turn_facts):
            tools_only = summary_from_turn_execution(turn_facts, state)
            reasoning_result = {
                "summary": tools_only,
                "confidence": 0.9,
                "risk_level": "LOW",
                "structured": {"source": "turn_facts"},
            }
            from app.services.code_artifact_pipeline import ensure_code_artifacts_quality

            payload = state.get("input_payload") or {}
            reasoning_result = ensure_code_artifacts_quality(
                reasoning_result,
                state,
                goal=str(payload.get("goal") or ""),
                budget_ctx=budget_ctx,
            )
            updated = merge_state(
                state,
                reasoning_result=reasoning_result,
                status=TaskStatus.REASONED.value,
                current_node="reasoning",
                audit_log=append_audit(
                    state,
                    "reasoning",
                    "success",
                    {"confidence": 0.9, "source": "turn_facts"},
                ),
            )
            get_state_store().save(updated)
            return updated

        force_llm = bool(payload.get("force_slow_reasoning"))
        fast = None if force_llm else try_fast_reasoning(state)
        reasoning_source = "fast"
        report_boundary("reasoning", "enter")

        if fast is not None:
            reasoning_result = {
                "summary": fast.get("summary", ""),
                "confidence": float(fast.get("confidence", 0.9)),
                "risk_level": str(fast.get("risk_level", "LOW")).upper(),
                "structured": fast.get("structured", {}),
            }
        else:
            if state.get("mission"):
                context = reasoning_context_from_observation(state)
            else:
                from app.services.fact_layer import reasoning_context_from_state

                context = reasoning_context_from_state(state)
            user_json = json.dumps(context, ensure_ascii=False)
            report_reasoning_context(state)
            report_status_trace("reasoning", "基于 turn_facts 综合回答（只读已执行事实）…")
            if trace_enabled() or answer_stream_enabled():
                raw = stream_llm_trace(
                    stream_structured(
                        "reasoning",
                        reasoning_system,
                        user_json,
                        budget_ctx=budget_ctx,
                        trace_state=state,
                        stream_node="reasoning",
                        stream_phase="reasoning_llm",
                    ),
                    node="reasoning",
                    phase="reasoning_llm",
                    field="summary",
                )
                result = extract_json_with_repair(
                    "reasoning",
                    raw,
                    prefer_keys=("summary",),
                    trace_state=state,
                    budget_ctx=budget_ctx,
                )
            else:
                result = invoke_structured(
                    "reasoning",
                    reasoning_system,
                    user_json,
                    budget_ctx=budget_ctx,
                    trace_state=state,
                )
            reasoning_source = "llm"
            structured = result.get("structured", {}) if isinstance(result.get("structured"), dict) else {}
            summary = str(result.get("summary") or "").strip()
            if not summary and (trace_enabled() or answer_stream_enabled()):
                summary = extract_field_text(raw, "summary").strip()
            if mode == "cot" and isinstance(structured.get("conclusion"), str):
                summary = structured["conclusion"] or summary
            reasoning_result = {
                "summary": summary,
                "confidence": float(result.get("confidence", 0.7)),
                "risk_level": str(result.get("risk_level", "LOW")).upper(),
                "structured": {**structured, "reasoning_mode": mode},
            }

        from app.services.code_artifact_pipeline import ensure_code_artifacts_quality

        payload = state.get("input_payload") or {}
        reasoning_result = ensure_code_artifacts_quality(
            reasoning_result,
            state,
            goal=str(payload.get("goal") or payload.get("query") or ""),
            budget_ctx=budget_ctx,
        )
        reasoning_result = apply_reasoning_guard(reasoning_result, turn_facts)
        structured_meta = reasoning_result.get("structured") or {}
        if answer_stream_enabled() and isinstance(structured_meta, dict):
            emit_final_artifact_fences(structured_meta)
        parser_repaired = bool(structured_meta.get("parser_repaired"))
        parser_fallback = bool(structured_meta.get("parser_fallback"))
        if parser_repaired:
            get_metrics_service().inc_reasoning_parser_event("repaired")
        if parser_fallback:
            get_metrics_service().inc_reasoning_parser_event("fallback")

        updated = budget_ctx.apply_to_state(
            merge_state(
                state,
                reasoning_result=reasoning_result,
                reasoning_mode=mode,
                status=TaskStatus.REASONED.value,
                current_node="reasoning",
                audit_log=append_audit(
                    state,
                    "reasoning",
                    "success",
                    {
                        "confidence": reasoning_result["confidence"],
                        "source": reasoning_source,
                        "reasoning_mode": mode,
                        "tokens_used": budget_ctx.tokens_used,
                        "memory_hits": len(state.get("memory_hits") or []),
                        "fact_warnings": (reasoning_result.get("structured") or {}).get("fact_warnings"),
                        "parser_repaired": parser_repaired,
                        "parser_fallback": parser_fallback,
                        "code_artifact_repaired": bool(
                            (reasoning_result.get("structured") or {}).get(
                                "code_artifact_repaired"
                            )
                        ),
                        "code_verify_ok": (reasoning_result.get("structured") or {}).get(
                            "code_verify_ok"
                        ),
                    },
                ),
            )
        )
        # 推理结果 trace 由 graph_runner.trace_after_node(reasoning) 统一发出，避免重复
        get_state_store().save(updated)
        return updated
    except BudgetExceededError as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"reasoning budget: {exc}"],
            status=TaskStatus.REASON_FAILED.value,
            current_node="reasoning",
            audit_log=append_audit(state, "reasoning", "budget_exceeded", {"detail": str(exc)}),
        )
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"reasoning: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.REASON_FAILED.value,
            current_node="reasoning",
            audit_log=append_audit(state, "reasoning", "error", {"detail": str(exc)}),
        )


