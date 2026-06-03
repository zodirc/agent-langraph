"""规划节点：图入口，决定路由与工具集。

调用方：app.runtime.graph、mission_act 内联流水线、reflection 重规划。
主流程：早退（TOOL_FAILED）→ 重规划输入 → mission 机械 skip_planning_llm
→ LLM 规划（skill planning_overlay、tool_selection）→ mission/writing 后处理
→ validate_plan、work_plan、route_audit → PLANNED。
写出：plan、selected_tools、writing_intent、skip_retrieval 等。

Planning node: graph entry; LLM or skip path; writes plan and tools for routers.
"""

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
    apply_planner_artifact_names,
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
from app.services.tool_registry import get_tool_registry


def _outline_status_for_planning(state: AgentState, payload: dict[str, Any]) -> dict[str, Any]:
    """Tell planning when outline is already materialized (steer should patch, not rewrite)."""
    from app.config.settings import settings

    ms = resolve_manuscript(state["task_id"], state.get("manuscript") or payload.get("manuscript"))
    stored = state.get("manuscript") or payload.get("manuscript") or {}
    outline_bytes = max(int(ms.outline_bytes or 0), int(stored.get("outline_bytes") or 0))
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    body_bytes = max(int(ms.body_bytes or 0), int(stored.get("body_bytes") or 0))
    return {
        "outline_path": ms.outline_path or stored.get("outline_path"),
        "outline_bytes": outline_bytes,
        "outline_complete": outline_bytes >= min_outline,
        "body_bytes": body_bytes,
        "steer_should_patch_not_rewrite": outline_bytes >= min_outline,
    }


def planning_node(state: AgentState) -> AgentState:
    """
    理解目标并产出结构化计划；写出 plan、selected_tools、writing_intent 等。

    Produce structured plan for the turn; next route_after_planning.
    """
    try:
        # If we're re-invoked from a snapshot where tool execution already exhausted retries,
        # do not "heal" the failure by re-planning and completing the task.
        # Let router send the run to dead_letter.
        from app.config.settings import settings

        if (
            str(state.get("status") or "") == TaskStatus.TOOL_FAILED.value
            and str(state.get("current_node") or "") == "tool_execution"
            and state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT
        ):
            return merge_state(state, current_node="planning")

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
        from app.services.mission_intervention import normalize_payload_execution_fields

        payload = normalize_payload_execution_fields(
            enrich_payload(
                dict(state.get("input_payload") or {}),
                task_id,
                session_turn=int(state.get("session_turn") or 1),
                manuscript=ms,
            )
        )
        payload.pop("turn_contract", None)
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
            payload = apply_planner_artifact_names(payload)
            trace_tools = ["writing_node"] if intent.get("enabled") else []
            from app.services.turn_kind import plan_steps_for_display

            skip_plan = plan_steps_for_display(
                merge_state(
                    state,
                    input_payload=payload,
                    mission=state.get("mission"),
                    progress=state.get("progress"),
                )
            )
            if not skip_plan:
                skip_plan = ["mission_writing_step"]
            report_plan_trace(
                skip_plan,
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
                plan=skip_plan,
                selected_tools=[],
                manuscript=ms.to_dict(),
                skip_retrieval=False,
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
        plan_validation_feedback = payload.get("plan_validation_feedback")
        goal_for_planning = str(
            payload.get("goal") or payload.get("query") or payload.get("question") or ""
        )
        mission_block_early = payload.get("mission") or state.get("mission") or {}
        from app.domain.packs.registry import resolve_mission_pack

        planning_pack = resolve_mission_pack(
            mission_kind=str(mission_block_early.get("kind") or ""),
            task_type=str(state.get("task_type") or ""),
            payload=payload,
        )
        from app.services.skill_task_attach import (
            merge_domain_pack_tools_with_skill,
            skill_tool_allowlist_from_state,
        )

        pack_tools = merge_domain_pack_tools_with_skill(
            list(planning_pack.tools or []) or None,
            skill_tool_allowlist_from_state(state),
        )
        runtime_caps = build_runtime_capabilities(
            goal=goal_for_planning,
            domain=str(planning_pack.name or ""),
            risk_level=str(payload.get("risk_level") or "LOW"),
            pack_tools=pack_tools,
            state=state,
        )
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
                "outline_status": _outline_status_for_planning(state, payload),
                "writing_instruction": payload.get("writing_instruction"),
                "previous_artifact_summary": payload.get("previous_artifact_summary"),
                "risk_level": payload.get("risk_level", "LOW"),
                "use_tools": payload.get("use_tools", True),
                "needs_search": payload.get("needs_search", True),
                "runtime_capabilities": runtime_caps,
                "existing_mission": payload.get("mission") or state.get("mission"),
                "route_audit_replan_feedback": replan_feedback,
                "plan_validation_feedback": plan_validation_feedback,
            },
            ensure_ascii=False,
        )
        from app.services.turn_contract import planning_fallback_from_state
        from app.services.turn_contract_lifecycle import contract_replan_required

        result: dict[str, Any] | None = None
        if contract_replan_required(payload):
            fb = planning_fallback_from_state(
                merge_state(state, input_payload=payload, manuscript=ms.to_dict())
            )
            if fb:
                result = dict(fb)

        if result is None:
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
            payload = normalize_payload_execution_fields({**payload, **patch})
        if result.get("force_slow_reasoning"):
            payload["force_slow_reasoning"] = True

        from app.services.mission.batch_unit_capability import enrich_planning_result_with_batch_unit

        result = enrich_planning_result_with_batch_unit(
            result, merge_state(state, input_payload=payload, manuscript=ms.to_dict())
        )

        from app.services.mission_intervention import apply_planning_intervention

        payload = apply_planning_intervention(
            result, payload, state=merge_state(state, input_payload=payload)
        )
        if steer_planning_turn:
            payload = complete_steer_planning(payload)
            from app.services.mission_intervention import intervention_from_payload
            from app.services.mission.steer_replan import apply_work_plan_patch
            from app.services.mission_orchestrator import ensure_work_plan, orchestration_enabled

            replan_state = merge_state(state, input_payload=payload)
            if orchestration_enabled(replan_state.get("mission") or {}):
                replan_state = ensure_work_plan(replan_state)
                replan_state = apply_work_plan_patch(
                    replan_state,
                    result.get("work_plan_patch"),
                    intervention=intervention_from_payload(payload),
                )
                state = merge_state(state, progress=replan_state.get("progress"))
                payload = dict(replan_state.get("input_payload") or payload)

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
                    state=merge_state(state, input_payload=payload),
                    task_id=state["task_id"],
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
        from app.services.tool_selection import validate_tool_selection

        user_role = str(payload.get("user_role") or "user")
        valid_tools, selection_issues = validate_tool_selection(
            all_tools,
            tool_params,
            get_tool_registry(),
            user_role,
        )
        if selection_issues:
            dropped_tools = list(dict.fromkeys(list(dropped_tools or []) + selection_issues))
        all_tools = valid_tools
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
                        state=merge_state(state, input_payload=payload),
                        task_id=state["task_id"],
                    ),
                    task_id=state["task_id"],
                )
        if patch_reason:
            mission_auto_reason = mission_auto_reason or patch_reason
            mission_block = dict(payload.get("mission") or mission_block or {})

        mission_in_state = dict(payload.get("mission") or state.get("mission") or {})
        if mission_in_state.get("kind") == "writing":
            from app.services.turn_contract import finalize_turn_execution_plan

            merged = merge_state(state, input_payload=payload, manuscript=ms.to_dict())
            payload, exec_tools = finalize_turn_execution_plan(
                result,
                payload,
                merged,
                exec_tools,
                mission=mission_in_state,
                steer_planning_turn=steer_planning_turn,
            )
        else:
            llm_intent = (
                dict(result.get("writing_intent"))
                if isinstance(result.get("writing_intent"), dict)
                else None
            )
            if llm_intent is None:
                llm_intent = {}
            if not llm_intent.get("action"):
                raw_action = str(
                    result.get("writing_action") or result.get("writing_mode") or ""
                ).strip()
                if raw_action:
                    llm_intent["action"] = raw_action
            if not llm_intent:
                llm_intent = None
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

        payload = apply_planner_artifact_names(payload, planning_result=result)

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
        from app.services.turn_kind import plan_steps_for_display

        authoritative_plan = plan_steps_for_display(
            merge_state(
                state,
                input_payload=payload,
                mission=payload.get("mission") or state.get("mission"),
                progress=state.get("progress"),
                plan=plan,
            )
        )
        if authoritative_plan and any(
            str(s).startswith(("contract:", "agenda:")) for s in authoritative_plan
        ):
            plan = authoritative_plan
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

        from app.services.intent_composer import bump_intent_revision

        payload = bump_intent_revision(payload)

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

        from app.services.plan_validator import validate_plan
        from app.services.turn_event_log import record_turn_event

        validation = validate_plan(plan, exec_tools, payload, updated)
        if validation.valid:
            updated = record_turn_event(
                updated,
                "plan_generated",
                "planning",
                "planning",
                {
                    "steps": len(plan),
                    "tools": exec_tools,
                },
            )
        else:
            updated = record_turn_event(
                updated,
                "plan_rejected",
                "planning",
                "planning",
                {"issues": validation.issues, "suggestions": validation.suggestions},
            )

        max_revisions = 2
        revisions = int(updated.get("planning_revision_count") or 0)
        if validation.should_replan and revisions < max_revisions:
            next_payload = dict(updated.get("input_payload") or {})
            next_payload["plan_validation_feedback"] = {
                "issues": validation.issues,
                "suggestions": validation.suggestions,
            }
            next_payload.pop("route_audit", None)
            return planning_node(
                merge_state(
                    updated,
                    input_payload=next_payload,
                    planning_revision_count=revisions + 1,
                    plan=None,
                    selected_tools=None,
                    status=TaskStatus.NEW.value,
                    audit_log=append_audit(
                        updated,
                        "planning",
                        "plan_validation_replan",
                        {"issues": validation.issues, "revision": revisions + 1},
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

        from app.services.mission_orchestrator import orchestration_enabled, work_plan_from_mission

        mission_for_agenda = updated.get("mission") or payload.get("mission") or {}
        if orchestration_enabled(mission_for_agenda) and plan:
            from app.services.task_agenda import agenda_summary, items_from_plan_steps, merge_agenda_into_work_plan

            agenda_items = items_from_plan_steps(plan, tool_dag=payload.get("tool_dag"))
            progress = dict(updated.get("progress") or {})
            base_plan = progress.get("work_plan") or work_plan_from_mission(mission_for_agenda)
            progress["work_plan"] = merge_agenda_into_work_plan(base_plan, agenda_items)
            progress["agenda_summary"] = agenda_summary(progress["work_plan"])
            updated = merge_state(updated, progress=progress)

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
