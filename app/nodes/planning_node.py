from __future__ import annotations

import json
from typing import Any

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.config.prompts import build_planning_system_prompt
from app.services.llm_client import extract_json_with_repair, invoke_structured, stream_structured
from app.services.resource_budget import (
    BudgetExceededError,
    budget_context_from_state,
    init_task_budget,
)
from app.services.manuscript_service import (
    build_writing_intent,
    enrich_payload,
    resolve_manuscript,
    split_execution_tools,
    strip_writing_content_from_tool_params,
)
from app.services.conversation_context import (
    conversation_history_for_llm,
    conversation_history_from_state,
)
from app.services.planning_tools import normalize_selected_tools
from app.services.reasoning_trace import (
    report_boundary,
    report_plan_trace,
    report_planning_input,
    report_status_trace,
    stream_llm_trace,
    trace_enabled,
    trace_verbose,
)
from app.services.mission_routing import (
    apply_planning_mission_decision,
    patch_mission_from_planning,
)
from app.services.mission_service import init_mission_state, should_run_mission_runtime
from app.services.runtime_capabilities import build_runtime_capabilities
from app.services.state_store import get_state_store
from app.services.stream_progress import report_progress


def planning_node(state: AgentState) -> AgentState:
    """
    Understand task goal and produce execution plan via LLM.

    Reads: input_payload, task_type
    Writes: plan, selected_tools, writing_intent, manuscript, status, current_node, audit_log
    """
    try:
        state = init_task_budget(state)
        budget_ctx = budget_context_from_state(state)
        payload_early = dict(state.get("input_payload") or {})
        reflection = state.get("reflection_result") or {}
        if reflection.get("retry_planning") or payload_early.get("route_audit_replan"):
            from app.services.route_audit.apply import build_replan_feedback

            audit = payload_early.get("route_audit") or {}
            payload_early["route_audit_replan_feedback"] = build_replan_feedback(audit)
            revisions = int(state.get("planning_revision_count") or 0) + 1
            state = merge_state(
                state,
                input_payload=payload_early,
                planning_revision_count=revisions,
                reflection_result={**reflection, "retry_planning": False},
            )
        report_progress("正在理解任务并制定计划…")
        report_boundary("planning", "enter")
        report_planning_input(state)
        report_status_trace("planning", "正在分析目标并生成结构化计划…")

        task_id = state["task_id"]
        ms = resolve_manuscript(task_id, state.get("manuscript"))
        payload = enrich_payload(
            dict(state.get("input_payload") or {}),
            task_id,
            session_turn=int(state.get("session_turn") or 1),
            manuscript=ms,
        )
        from app.services.mission_steer import complete_steer_planning, steer_requires_planning

        steer_planning_turn = steer_requires_planning(payload)
        mission_before_steer = dict(state.get("mission") or payload.get("mission") or {})

        if (
            payload.get("skip_planning_llm")
            and state.get("mission")
            and not steer_requires_planning(payload)
        ):
            from app.services.mission_schema import resolve_writing_intent_for_step

            mission_in_state = state.get("mission") or {}
            if mission_in_state.get("kind") == "writing":
                payload["writing_intent"] = resolve_writing_intent_for_step(
                    merge_state(state, input_payload=payload, manuscript=ms.to_dict()),
                    mission=mission_in_state,
                )
            intent = payload.get("writing_intent") or {}
            trace_tools = ["writing_node"] if intent.get("enabled") else []
            report_plan_trace(
                [],
                trace_tools,
                meta={
                    "跳过检索": bool(state.get("skip_retrieval")),
                    "writing_action": intent.get("action"),
                    "mission": True,
                    "body_path": ms.body_path,
                    "planning": "mission_step_skip",
                },
            )
            updated = merge_state(
                state,
                input_payload=payload,
                plan=["mission_writing_step"],
                selected_tools=[],
                manuscript=ms.to_dict(),
                skip_retrieval=True,
                status=TaskStatus.PLANNED.value,
                current_node="planning",
                audit_log=append_audit(
                    state,
                    "planning",
                    "mission_step_skip",
                    {"writing_intent": intent, "mission_step": state.get("mission_step")},
                ),
            )
            from app.services.route_audit.pipeline import run_route_audit_pipeline

            updated = run_route_audit_pipeline(updated)
            get_state_store().save(updated)
            return updated

        replan_feedback = payload.get("route_audit_replan_feedback")
        user_content = json.dumps(
            {
                "task_type": state.get("task_type"),
                "goal": payload.get("goal") or payload.get("query") or payload.get("question"),
                "context": payload.get("context", {}),
                "conversation_history": conversation_history_for_llm(
                    conversation_history_from_state(state)
                ),
                "session_turn": state.get("session_turn"),
                "manuscript": payload.get("manuscript"),
                "writing_instruction": payload.get("writing_instruction"),
                "previous_artifact_summary": payload.get("previous_artifact_summary"),
                "risk_level": payload.get("risk_level", "LOW"),
                "use_tools": payload.get("use_tools", True),
                "needs_search": payload.get("needs_search", True),
                "runtime_capabilities": build_runtime_capabilities(),
                "existing_mission": payload.get("mission") or state.get("mission"),
                "route_audit_replan_feedback": replan_feedback,
            },
            ensure_ascii=False,
        )
        system_prompt = build_planning_system_prompt(state)
        if trace_enabled():
            raw = stream_llm_trace(
                stream_structured(
                    "planning",
                    system_prompt,
                    user_content,
                    budget_ctx=budget_ctx,
                    trace_state=state,
                    stream_node="planning",
                    stream_phase="planning_llm",
                ),
                node="planning",
                phase="planning_llm",
                field="plan",
                extra_fields=["risk_level"] if trace_verbose() else None,
            )
            result = extract_json_with_repair(
                "planning",
                raw,
                prefer_keys=("plan",),
                trace_state=state,
                budget_ctx=budget_ctx,
            )
        else:
            result = invoke_structured(
                "planning",
                system_prompt,
                user_content,
                budget_ctx=budget_ctx,
                trace_state=state,
            )

        payload_patch = result.pop("_input_payload_patch", None)
        if payload_patch:
            patch = dict(payload_patch)
            patch.pop("force_slow_reasoning", None)
            payload = {**payload, **patch}
        if result.get("force_slow_reasoning"):
            payload["force_slow_reasoning"] = True

        from app.services.mission_intervention import apply_planning_intervention

        payload = apply_planning_intervention(result, payload)
        if steer_planning_turn:
            payload = complete_steer_planning(payload)
            from app.services.mission_steer_confirm import (
                apply_steer_confirmation_pending,
                build_steer_intent_summary,
                steer_confirmation_required,
            )

            if steer_confirmation_required(
                result,
                payload,
                mission_before=mission_before_steer,
            ):
                summary = build_steer_intent_summary(
                    result,
                    payload,
                    mission_before=mission_before_steer,
                )
                payload = apply_steer_confirmation_pending(
                    payload, summary, task_id=state["task_id"]
                )

        from app.services.mission_steer import apply_review_outline_mode, review_outline_requested

        if review_outline_requested(payload):
            payload = apply_review_outline_mode(
                payload,
                payload.get("mission") or state.get("mission") or {},
            )

        plan_tool_params = result.get("tool_params")
        tool_params = dict(payload.get("tool_params") or {})
        if isinstance(plan_tool_params, dict):
            for key, value in plan_tool_params.items():
                if isinstance(value, dict):
                    tool_params[key] = {**(tool_params.get(key) or {}), **value}
                else:
                    tool_params[key] = value
        tool_params = strip_writing_content_from_tool_params(tool_params)

        from app.services.llm_client import normalize_planning_plan

        plan = normalize_planning_plan(result.get("plan", []))
        if len(plan) >= 10 and sum(1 for s in plan if "append" in s.lower()) >= 6:
            plan = normalize_planning_plan(
                ["write_outline via writing", "append body via mission loop"]
            )
            result = {
                **result,
                "mission_recommended": True,
                "use_mission": True,
            }
        raw_tools = [str(tool) for tool in result.get("selected_tools", [])]
        all_tools, dropped_tools = normalize_selected_tools(raw_tools)
        exec_tools, writing_tools = split_execution_tools(all_tools)

        goal = str(payload.get("goal") or "")
        mission_before_patch = dict(payload.get("mission") or state.get("mission") or {})
        payload, mission_auto_reason = apply_planning_mission_decision(result, payload)
        plan_mission = result.get("mission")
        mission_block: dict[str, Any] | None = None
        if isinstance(plan_mission, dict) and plan_mission:
            mission_block = {**(payload.get("mission") or {}), **plan_mission}
            payload["mission"] = mission_block
        elif payload.get("mission"):
            mission_block = dict(payload["mission"])

        payload, patch_reason = patch_mission_from_planning(
            result,
            payload,
            state_mission=payload.get("mission") or state.get("mission"),
        )
        if patch_reason and steer_planning_turn and not payload.get("steer_intent_pending_confirm"):
            from app.services.mission_steer_confirm import (
                apply_steer_confirmation_pending,
                build_steer_intent_summary,
                steer_confirmation_required,
            )

            if steer_confirmation_required(
                result,
                payload,
                mission_before=mission_before_patch,
            ):
                payload = apply_steer_confirmation_pending(
                    payload,
                    build_steer_intent_summary(
                        result,
                        payload,
                        mission_before=mission_before_patch,
                    ),
                    task_id=state["task_id"],
                )
        if patch_reason:
            mission_auto_reason = mission_auto_reason or patch_reason
            mission_block = dict(payload.get("mission") or mission_block or {})

        mission_in_state = dict(payload.get("mission") or state.get("mission") or {})
        if mission_in_state.get("kind") == "writing":
            from app.services.mission_schema import resolve_writing_intent_for_step

            payload["writing_intent"] = resolve_writing_intent_for_step(
                merge_state(state, input_payload=payload, manuscript=ms.to_dict()),
                mission=mission_in_state,
            )
        else:
            llm_intent = (
                result.get("writing_intent")
                if isinstance(result.get("writing_intent"), dict)
                else None
            )
            payload["writing_intent"] = build_writing_intent(
                goal=goal,
                selected_tools=writing_tools or all_tools,
                manuscript=ms,
                session_turn=int(state.get("session_turn") or 1),
                llm_intent=llm_intent,
                mission_block=mission_block,
                active_mission=state.get("mission"),
                mission_step=int(state.get("mission_step") or 0),
            )

        payload["tool_params"] = tool_params

        tool_stages = result.get("tool_stages")
        if isinstance(tool_stages, list) and tool_stages:
            payload["tool_stages"] = tool_stages
        elif len(exec_tools) > 1 and not tool_stages:
            independent = {"calculator", "get_runtime_info", "echo", "read_text_artifact"}
            parallel = [t for t in exec_tools if t in independent]
            sequential = [t for t in exec_tools if t not in parallel]
            stages: list[list[str]] = []
            if parallel:
                stages.append(parallel)
            for t in sequential:
                stages.append([t])
            if stages:
                payload["tool_stages"] = stages

        tool_dag = result.get("tool_dag")
        if isinstance(tool_dag, dict) and tool_dag.get("nodes"):
            payload["tool_dag"] = tool_dag

        review_required = str(result.get("risk_level", "LOW")).upper() in ("HIGH", "CRITICAL")
        skip_retrieval = bool(result.get("skip_retrieval", False))

        trace_tools = list(exec_tools)
        if (payload.get("writing_intent") or {}).get("enabled"):
            trace_tools.append("writing_node")
        elif writing_tools:
            trace_tools.extend(writing_tools)
        if mission_block:
            trace_tools.append("mission_runtime")
        report_plan_trace(
            plan,
            trace_tools,
            dropped=dropped_tools or None,
            meta={
                "风险等级": result.get("risk_level"),
                "跳过检索": skip_retrieval,
                "writing_action": (payload.get("writing_intent") or {}).get("action"),
                "mission": bool(mission_block),
                "mission_auto": mission_auto_reason or "",
                "body_path": ms.body_path,
            },
        )

        updated = budget_ctx.apply_to_state(
            merge_state(
                state,
                input_payload=payload,
                mission=payload.get("mission") or state.get("mission"),
                plan=plan,
                selected_tools=exec_tools,
                manuscript=ms.to_dict(),
                skip_retrieval=skip_retrieval,
                review_required=review_required,
                status=TaskStatus.PLANNED.value,
                current_node="planning",
                audit_log=append_audit(
                    state,
                    "planning",
                    "success",
                    {
                        "plan_steps": len(plan),
                        "tools": exec_tools,
                        "writing_intent": payload.get("writing_intent"),
                        "mission_planned": bool(mission_block),
                        "dropped_tools": dropped_tools,
                        "source": "llm",
                        "skip_retrieval": skip_retrieval,
                        "tokens_used": budget_ctx.tokens_used,
                    },
                ),
            )
        )
        if should_run_mission_runtime(updated, payload) and not state.get("mission"):
            updated = init_mission_state(updated, payload)
            updated = merge_state(
                updated,
                status=TaskStatus.MISSION_RUNNING.value,
                audit_log=append_audit(
                    updated,
                    "planning",
                    "mission_runtime",
                    {"execution_mode": updated.get("execution_mode")},
                ),
            )
        from app.services.route_audit.pipeline import run_route_audit_pipeline

        updated = run_route_audit_pipeline(updated)
        get_state_store().save(updated)
        return updated
    except BudgetExceededError as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"planning budget: {exc}"],
            status=TaskStatus.FAILED.value,
            current_node="planning",
            audit_log=append_audit(state, "planning", "budget_exceeded", {"detail": str(exc)}),
        )
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"planning: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="planning",
            audit_log=append_audit(state, "planning", "error", {"detail": str(exc)}),
        )
