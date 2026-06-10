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
from app.services.thin_execution import reasoning_llm_purpose
from app.services.fact_layer import (
    apply_reasoning_guard,
    attach_turn_facts,
    build_turn_facts,
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


def _build_reasoning_result(result: dict, raw: str, mode: str) -> dict[str, object]:
    structured = result.get("structured") if isinstance(result.get("structured"), dict) else {}
    summary = str(result.get("summary") or "").strip()
    if not summary and raw and (trace_enabled() or answer_stream_enabled()):
        summary = extract_field_text(raw, "summary").strip()
    if mode == "cot" and isinstance(structured.get("conclusion"), str):
        summary = structured["conclusion"] or summary
    return {
        "summary": summary,
        "confidence": float(result.get("confidence", 0.7)),
        "risk_level": str(result.get("risk_level", "LOW")).upper(),
        "structured": {**structured, "reasoning_mode": mode},
    }


def _run_reasoning_llm_loop(
    state: AgentState,
    *,
    context: dict[str, object],
    reasoning_system: str,
    llm_purpose: str,
    mode: str,
    budget_ctx: object,
) -> tuple[dict[str, object], str, AgentState]:
    from app.services.execution_control import controlled_iter
    from app.services.thinking_retry_signals import (
        build_reasoning_retry_context,
        max_thinking_retries,
        reasoning_result_needs_retry,
    )

    working = state
    raw = ""
    reasoning_result: dict[str, object] = {}
    attempts = max_thinking_retries()
    stream_allowed = trace_enabled() or answer_stream_enabled()

    for attempt in range(attempts):
        ctx = build_reasoning_retry_context(context, working) if attempt else context
        user_json = json.dumps(ctx, ensure_ascii=False)
        # First attempt streams thinking/answer to the user; silent retries only after.
        use_stream = stream_allowed and attempt == 0
        if use_stream:
            raw = stream_llm_trace(
                controlled_iter(
                    str(state["task_id"]),
                    stream_structured(
                        llm_purpose,
                        reasoning_system,
                        user_json,
                        budget_ctx=budget_ctx,
                        trace_state=working,
                        stream_node="reasoning",
                        stream_phase="reasoning_llm",
                    ),
                    phase="reasoning_stream_chunk",
                ),
                node="reasoning",
                phase="reasoning_llm",
                field="summary",
            )
            # Parse as reasoning so truncated/malformed output degrades via
            # reasoning repair+fallback instead of re-raising. ``llm_purpose``
            # only selects the LLM token-budget tier (thin QA → "routing").
            result = extract_json_with_repair(
                "reasoning",
                raw,
                prefer_keys=("summary",),
                trace_state=working,
                budget_ctx=budget_ctx,
            )
        else:
            result = invoke_structured(
                llm_purpose,
                reasoning_system,
                user_json,
                budget_ctx=budget_ctx,
                trace_state=working,
                parse_purpose="reasoning",
            )
            raw = json.dumps(result, ensure_ascii=False)
        reasoning_result = _build_reasoning_result(result, raw, mode)
        working = merge_state(working, reasoning_result=reasoning_result)
        if not reasoning_result_needs_retry(reasoning_result):
            break
    return reasoning_result, raw, working


def _finalize_reasoning_with_guards(
    state: AgentState,
    reasoning_result: dict[str, object],
    *,
    reasoning_source: str,
    mode: str,
    budget_ctx: object,
    parser_repaired: bool,
    parser_fallback: bool,
) -> AgentState:
    from app.services.post_generation_guard import apply_post_generation_guard
    from app.services.stream_output_guard import consume_stream_guard_result

    stream_guard = consume_stream_guard_result()
    if stream_guard:
        issues = list(stream_guard.get("issues") or [])
        updated = merge_state(
            state,
            reasoning_result=reasoning_result,
            output_guard_result=stream_guard,
            status=TaskStatus.REJECTED.value,
            policy_result="REJECT",
            errors=list(state.get("errors", [])) + [f"output_guard: {', '.join(issues)}"],
            current_node="reasoning",
            audit_log=append_audit(
                state,
                "reasoning",
                "stream_guard_blocked",
                {"issues": issues},
            ),
        )
        get_state_store().save(updated)
        return updated

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
                    "fact_warnings": (reasoning_result.get("structured") or {}).get(
                        "fact_warnings"
                    ),
                    "parser_repaired": parser_repaired,
                    "parser_fallback": parser_fallback,
                    "code_artifact_repaired": bool(
                        (reasoning_result.get("structured") or {}).get("code_artifact_repaired")
                    ),
                    "code_verify_ok": (reasoning_result.get("structured") or {}).get(
                        "code_verify_ok"
                    ),
                },
            ),
        )
    )
    updated = apply_post_generation_guard(updated)
    get_state_store().save(updated)
    return updated


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
        from app.services.reasoning_grounding import build_grounding_instructions

        grounding_overlay = build_grounding_instructions(state)
        if grounding_overlay:
            reasoning_system = f"{reasoning_system}\n\n[Evidence policy]\n{grounding_overlay}"
        state = attach_turn_facts(state)
        payload = state.get("input_payload", {})
        turn_facts = state.get("turn_facts") or build_turn_facts(state)
        force_llm = bool(payload.get("force_slow_reasoning"))

        from app.services.turn_contract import (
            contract_requires_side_effects,
            is_turn_contract_fulfilled,
        )

        if (
            contract_requires_side_effects(payload, state=state)
            and not is_turn_contract_fulfilled(state)
            and not force_llm
        ):
            updated = merge_state(
                state,
                reasoning_result={
                    "summary": (
                        "本回合执行契约尚未完成（需先运行工具或写作阶段），"
                        "不会用推理代替实际审阅/修改。"
                    ),
                    "confidence": 0.85,
                    "risk_level": "MEDIUM",
                    "structured": {"source": "contract_unfulfilled"},
                },
                status=TaskStatus.REASONED.value,
                current_node="reasoning",
                audit_log=append_audit(
                    state,
                    "reasoning",
                    "contract_unfulfilled",
                    {"blocked_llm": True},
                ),
            )
            from app.services.post_generation_guard import apply_post_generation_guard

            updated = apply_post_generation_guard(updated)
            get_state_store().save(updated)
            return updated

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
            from app.services.post_generation_guard import apply_post_generation_guard

            updated = apply_post_generation_guard(updated)
            get_state_store().save(updated)
            return updated

        fast = None if force_llm else try_fast_reasoning(state)
        reasoning_source = "fast"
        llm_purpose = reasoning_llm_purpose(state)
        report_boundary("reasoning", "enter")

        if fast is not None:
            reasoning_result = {
                "summary": fast.get("summary", ""),
                "confidence": float(fast.get("confidence", 0.9)),
                "risk_level": str(fast.get("risk_level", "LOW")).upper(),
                "structured": fast.get("structured", {}),
            }
        else:
            from app.services.fact_layer import reasoning_context_from_state

            context = reasoning_context_from_state(state)
            report_reasoning_context(state)
            report_status_trace("reasoning", "基于 turn_facts 综合回答（只读已执行事实）…")
            reasoning_result, _raw, _working = _run_reasoning_llm_loop(
                state,
                context=context,
                reasoning_system=reasoning_system,
                llm_purpose=llm_purpose,
                mode=mode,
                budget_ctx=budget_ctx,
            )
            reasoning_source = "llm"

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

        return _finalize_reasoning_with_guards(
            state,
            reasoning_result,
            reasoning_source=reasoning_source,
            mode=mode,
            budget_ctx=budget_ctx,
            parser_repaired=parser_repaired,
            parser_fallback=parser_fallback,
        )
    except BudgetExceededError as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"reasoning budget: {exc}"],
            status=TaskStatus.REASON_FAILED.value,
            current_node="reasoning",
            audit_log=append_audit(state, "reasoning", "budget_exceeded", {"detail": str(exc)}),
        )
    except Exception as exc:
        from app.services.execution_control import (
            PAUSE_USER_REQUESTED_CANCEL,
            PAUSE_USER_REQUESTED_PAUSE,
            CancelRequested,
            PauseRequested,
        )

        if isinstance(exc, PauseRequested):
            updated = merge_state(
                state,
                status=TaskStatus.PAUSED.value,
                current_node="reasoning",
                mission_control={"pause_reason": PAUSE_USER_REQUESTED_PAUSE, "reason": str(exc)},
                audit_log=append_audit(
                    state, "reasoning", "control_interrupt", {"detail": str(exc)}
                ),
            )
            get_state_store().save(updated)
            return updated
        if isinstance(exc, CancelRequested):
            updated = merge_state(
                state,
                status=TaskStatus.CANCELLED.value,
                current_node="reasoning",
                mission_control={"pause_reason": PAUSE_USER_REQUESTED_CANCEL, "reason": str(exc)},
                audit_log=append_audit(
                    state, "reasoning", "control_interrupt", {"detail": str(exc)}
                ),
            )
            get_state_store().save(updated)
            return updated
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"reasoning: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.REASON_FAILED.value,
            current_node="reasoning",
            audit_log=append_audit(state, "reasoning", "error", {"detail": str(exc)}),
        )


