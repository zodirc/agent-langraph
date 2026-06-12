"""规划节点（统一内核）：意图 → Action 序列，识别即执行。

主流程：早退（TOOL_FAILED 重试耗尽）→ 预规划（模式解析）→ QA / 工程薄路径
→ LLM 结构化规划（产出 actions）→ 工具校验 → route_audit → PLANNED。

写出：``plan``（人读摘要）、``planned_actions``（统一 Action 列表）、
``selected_tools`` / ``tool_params`` / ``tool_stages``（过渡期执行传输层，
由 actions 直接渲染，零翻译）、``skip_retrieval``。

不再产出 mission / writing_intent / turn_contract 等旧控制面字段。
"""

from __future__ import annotations

import json
from typing import Any

from app.config.prompts import build_planning_system_prompt
from app.domain.action import Action
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_resolver import manifest_for_planning
from app.services.conversation_context import (
    conversation_history_for_llm,
    conversation_history_from_state,
)
from app.services.llm_client import (
    extract_json_with_repair,
    invoke_structured,
    normalize_planning_plan,
    stream_structured,
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
from app.services.resource_budget import (
    BudgetExceededError,
    budget_context_from_state,
    init_task_budget,
)
from app.services.runtime_capabilities import build_runtime_capabilities
from app.services.state_store import get_state_store
from app.services.stream_progress import report_progress
from app.services.tool_registry import get_tool_registry

# Action types executed by nodes (not via selected_tools transport).
_NODE_HANDLED = ("answer", "run_code")


def _actions_from_result(result: dict[str, Any]) -> tuple[list[Action], list[str]]:
    """Parse the planner's ``actions`` into validated Action objects."""
    raw = result.get("actions")
    if not isinstance(raw, list):
        return [], []
    actions: list[Action] = []
    issues: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            issues.append("action_not_object")
            continue
        try:
            actions.append(Action.from_dict(item))
        except (TypeError, ValueError) as exc:
            issues.append(f"invalid_action:{exc}")
    return actions, issues


def _execution_transport_from_actions(
    actions: list[Action],
) -> tuple[list[str], dict[str, dict[str, Any]], list[list[str]]]:
    """Render actions into the legacy tool transport (zero re-interpretation).

    Until tool_node consumes ``planned_actions`` directly (WP-5), artifact and
    run_tool actions ride on selected_tools + tool_params + tool_stages.
    """
    tools: list[str] = []
    tool_params: dict[str, dict[str, Any]] = {}
    stages: list[list[str]] = []
    for action in actions:
        if action.type in _NODE_HANDLED or action.type == "retrieve":
            continue
        if action.type == "run_tool":
            name = str(action.params.get("name") or "")
            params = {k: v for k, v in action.params.items() if k != "name"}
        else:
            name = action.tool_name or ""
            params = dict(action.params)
        if not name:
            continue
        if name not in tools:
            tools.append(name)
            stages.append([name])
        tool_params[name] = {**tool_params.get(name, {}), **params}
    return tools, tool_params, stages


def _finish_thin(
    state: AgentState,
    *,
    payload: dict[str, Any],
    plan: list[str],
    tools: list[str],
    planned_actions: list[Action],
    audit_action: str,
    audit_detail: dict[str, Any],
    pin_mode: bool = True,
    skip_retrieval: bool = True,
) -> AgentState:
    """Common tail for thin (LLM-free) planning paths."""
    updated = merge_state(
        state,
        input_payload=payload,
        plan=plan,
        planned_actions=[a.to_dict() for a in planned_actions] or None,
        selected_tools=tools,
        skip_retrieval=skip_retrieval,
        review_required=False,
        status=TaskStatus.PLANNED.value,
        current_node="planning",
        audit_log=append_audit(state, "planning", audit_action, audit_detail),
    )
    from app.services.route_audit.pipeline import run_route_audit_pipeline

    pinned_mode = str(payload.get("target_mode") or "")
    updated = run_route_audit_pipeline(updated)
    if pin_mode and pinned_mode:
        lp = dict(updated.get("input_payload") or {})
        lp["target_mode"] = pinned_mode
        lp["current_mode"] = pinned_mode
        updated = merge_state(updated, input_payload=lp)
    get_state_store().save(updated)
    return updated


def planning_node(state: AgentState) -> AgentState:
    """理解目标并产出 Action 序列；写出 plan、planned_actions、selected_tools。"""
    try:
        from app.config.settings import settings

        # Re-invoked from a snapshot where tool execution exhausted retries:
        # do not "heal" the failure by re-planning; router sends to dead_letter.
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
        report_status_trace("planning", "预规划：路由审计与意图判定…")

        from app.services.mode_freeze import record_planning_mode_entry

        record_planning_mode_entry(state, phase="planning_enter")

        payload = dict(state.get("input_payload") or {})
        payload.pop("turn_contract", None)
        state = merge_state(state, input_payload=payload)

        from app.services.pre_planning import (
            engineering_thin_plan,
            engineering_thin_tools,
            qa_thin_plan,
            run_pre_planning_pipeline,
            session_source_inquiry_thin_actions,
            should_skip_planning_llm,
            should_skip_qa_planning_llm,
            should_skip_session_source_inquiry_planning,
        )

        state = run_pre_planning_pipeline(state)
        payload = dict(state.get("input_payload") or {})

        # --- Session source inquiry: retrieve source domain, then answer (no planning LLM) ---
        if should_skip_session_source_inquiry_planning(state):
            report_status_trace("planning", "素材确认问答：跳过完整规划 LLM，走薄路径")
            goal = str(payload.get("goal") or payload.get("query") or "").strip()
            actions = session_source_inquiry_thin_actions(state)
            payload["writing_intent"] = {
                "enabled": False,
                "blocked_by": "session_source_qa",
                "source": "thin_planning",
            }
            payload["thin_execution_profile"] = "session_source_qa"
            payload["turn_kind"] = "narrate_only"
            payload.pop("require_planning_after_steer", None)
            plan = ["retrieve session source", "answer about loaded material"]
            report_plan_trace(
                plan,
                [],
                meta={
                    "planning": "session_source_inquiry_thin",
                    "target_mode": payload.get("target_mode"),
                    "skip_retrieval": False,
                },
            )
            return _finish_thin(
                state,
                payload=payload,
                plan=plan,
                tools=[],
                planned_actions=actions,
                audit_action="session_source_inquiry_thin",
                audit_detail={
                    "target_mode": payload.get("target_mode"),
                    "goal_preview": goal[:80],
                },
                pin_mode=False,
                skip_retrieval=False,
            )

        # --- Artifact edit thin path: polish/revise existing file (before QA thin) ---
        from app.services.artifact_edit_intent import detect_artifact_edit_intent

        goal = str(payload.get("goal") or payload.get("query") or "").strip()
        if getattr(settings, "ARTIFACT_EDIT_FAST_PATH", True) and detect_artifact_edit_intent(
            state, goal
        ):
            from app.services.pre_planning import artifact_edit_thin_actions

            actions = artifact_edit_thin_actions(state)
            from app.services.metrics_service import get_metrics_service

            metrics = get_metrics_service()
            if not actions:
                metrics.inc_contract_event("artifact_edit_ambiguous_multi")
            if actions:
                exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
                payload["tool_params"] = {**payload.get("tool_params", {}), **tool_params}
                payload["tool_stages"] = stages
                payload["thin_execution_profile"] = "artifact_edit"
                payload["writing_intent"] = {
                    "enabled": True,
                    "source": "artifact_edit",
                }
                plan = ["读取已有产物", "生成修订内容", "写回文件"]
                report_plan_trace(
                    plan,
                    exec_tools,
                    meta={
                        "planning": "artifact_edit_thin",
                        "target_mode": payload.get("target_mode"),
                        "goal_preview": goal[:80],
                    },
                )
                metrics.inc_contract_event("artifact_edit_fast_path")
                metrics.inc_contract_event("artifact_edit_resolved_single")
                return _finish_thin(
                    state,
                    payload=payload,
                    plan=plan,
                    tools=exec_tools,
                    planned_actions=actions,
                    audit_action="artifact_edit_thin",
                    audit_detail={
                        "target_mode": payload.get("target_mode"),
                        "goal_preview": goal[:80],
                    },
                    pin_mode=False,
                )

        # --- QA thin path: conversational QA never pays planning latency ---
        if should_skip_qa_planning_llm(state):
            goal = str(payload.get("goal") or payload.get("query") or "").strip()
            payload["writing_intent"] = {
                "enabled": False,
                "blocked_by": "qa_mode",
                "source": "thin_planning",
            }
            payload["thin_execution_profile"] = "qa_direct"
            plan = qa_thin_plan(goal, intent_kind=str(payload.get("intent_kind") or "qa"))
            actions = [Action(type="answer", completes_turn=True, source="structural")]
            report_plan_trace(
                plan,
                [],
                meta={
                    "planning": "qa_thin_skip",
                    "target_mode": payload.get("target_mode"),
                    "intent_kind": payload.get("intent_kind"),
                    "skip_retrieval": True,
                },
            )
            return _finish_thin(
                state,
                payload=payload,
                plan=plan,
                tools=[],
                planned_actions=actions,
                audit_action="qa_thin_skip",
                audit_detail={
                    "target_mode": payload.get("target_mode"),
                    "intent_kind": payload.get("intent_kind"),
                    "goal_preview": goal[:80],
                },
            )

        # --- Engineering thin path: repo-style delivery skips planning LLM ---
        if should_skip_planning_llm(state):
            payload["writing_intent"] = {
                "enabled": False,
                "blocked_by": "engineering_mode",
                "source": "thin_planning",
            }
            plan = engineering_thin_plan()
            tools = engineering_thin_tools(state)
            goal = str(payload.get("goal") or payload.get("query") or "")
            actions = [
                Action(
                    type="run_code",
                    params={"goal": goal},
                    completes_turn=True,
                    source="structural",
                )
            ]
            report_plan_trace(
                plan,
                tools,
                meta={
                    "planning": "engineering_thin_skip",
                    "target_mode": payload.get("target_mode"),
                    "intent_kind": payload.get("intent_kind"),
                    "skip_retrieval": True,
                },
            )
            return _finish_thin(
                state,
                payload=payload,
                plan=plan,
                tools=tools,
                planned_actions=actions,
                audit_action="engineering_thin_skip",
                audit_detail={
                    "target_mode": payload.get("target_mode"),
                    "intent_kind": payload.get("intent_kind"),
                },
            )

        # --- Writing playbook thin path: classified operator skips planning LLM ---
        from app.services.writing_intent_classifier import classify_writing_operator
        from app.services.writing_playbook import apply_writing_playbook
        from app.services.writing_project import (
            ensure_writing_project,
            writing_project_manifest_exists,
        )

        goal = str(payload.get("goal") or payload.get("query") or "").strip()
        task_id = str(state["task_id"])
        operator = classify_writing_operator(goal, state)
        mode = str(payload.get("target_mode") or payload.get("current_mode") or "").lower()
        if operator and not payload.get("latest_steer_message") and not payload.get(
            "planning_must_run_llm"
        ):
            if mode == "manuscript_mode" and not writing_project_manifest_exists(task_id):
                ensure_writing_project(task_id, goal=goal)
            if writing_project_manifest_exists(task_id):
                actions, plan, patched = apply_writing_playbook(
                    [],
                    operator=operator,
                    goal=goal,
                    task_id=task_id,
                    state=state,
                )
                if actions and patched:
                    from app.services.metrics_service import get_metrics_service

                    exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
                    payload["tool_params"] = {**payload.get("tool_params", {}), **tool_params}
                    payload["tool_stages"] = stages
                    payload["writing_operator"] = operator
                    payload["writing_intent"] = {
                        "enabled": True,
                        "source": "writing_playbook_thin",
                    }
                    payload["thin_execution_profile"] = "writing_playbook"
                    report_plan_trace(
                        plan,
                        exec_tools,
                        meta={
                            "planning": "writing_playbook_thin",
                            "writing_operator": operator,
                            "skip_retrieval": False,
                        },
                    )
                    get_metrics_service().inc_contract_event(f"writing_playbook_{operator}")
                    return _finish_thin(
                        state,
                        payload=payload,
                        plan=plan,
                        tools=exec_tools,
                        planned_actions=actions,
                        audit_action="writing_playbook_thin",
                        audit_detail={
                            "writing_operator": operator,
                            "goal_preview": goal[:80],
                        },
                        pin_mode=True,
                        skip_retrieval=False,
                    )

        # --- Full LLM planning: goal → actions ---
        report_status_trace("planning", "正在调用规划模型生成结构化计划…")
        goal_for_planning = str(
            payload.get("latest_steer_message")
            or payload.get("goal")
            or payload.get("query")
            or payload.get("question")
            or ""
        ).strip()

        from app.services.skill_task_attach import (
            merge_domain_pack_tools_with_skill,
            skill_tool_allowlist_from_state,
        )

        pack_tools = merge_domain_pack_tools_with_skill(
            None,
            skill_tool_allowlist_from_state(state),
        )
        runtime_caps = build_runtime_capabilities(
            goal=goal_for_planning,
            domain="",
            risk_level=str(payload.get("risk_level") or "LOW"),
            pack_tools=pack_tools,
            state=state,
        )
        planning_payload = {
            "task_type": state.get("task_type"),
            "goal": goal_for_planning,
            "context": payload.get("context", {}),
            "conversation_history": [],
            "session_turn": state.get("session_turn"),
            **manifest_for_planning(state, payload),
            "previous_artifact_summary": payload.get("previous_artifact_summary"),
            "risk_level": payload.get("risk_level", "LOW"),
            "use_tools": payload.get("use_tools", True),
            "needs_search": payload.get("needs_search", True),
            "runtime_capabilities": runtime_caps,
            "route_audit_replan_feedback": payload.get("route_audit_replan_feedback"),
            "plan_validation_feedback": payload.get("plan_validation_feedback"),
            "intent_revision": payload.get("intent_revision"),
        }
        from app.services.prompt_context_gateway import (
            context_governance_enabled,
            prepare_governed_user_json,
        )

        if context_governance_enabled():
            user_content = prepare_governed_user_json(state, "planning", planning_payload)
        else:
            planning_payload["conversation_history"] = conversation_history_for_llm(
                conversation_history_from_state(state)
            )
            user_content = json.dumps(planning_payload, ensure_ascii=False)

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

        result.pop("_input_payload_patch", None)

        # --- Map planning result → Action sequence (the core contract) ---
        actions, action_issues = _actions_from_result(result)
        plan = normalize_planning_plan(result.get("plan", []))
        from app.services.artifact_edit_intent import ensure_artifact_edit_write_action
        from app.services.artifact_rename_intent import normalize_rename_actions

        actions = normalize_rename_actions(actions, goal=goal_for_planning)
        from app.services.writing_playbook import classify_and_apply_playbook

        actions, playbook_plan, writing_operator, playbook_patched = classify_and_apply_playbook(
            actions,
            goal=goal_for_planning,
            task_id=str(state["task_id"]),
            state=state,
            payload=payload,
        )
        if writing_operator:
            payload["writing_operator"] = writing_operator
        if playbook_patched and playbook_plan:
            plan = playbook_plan
        actions, write_patched = ensure_artifact_edit_write_action(
            actions,
            plan,
            goal=goal_for_planning,
            task_id=str(state["task_id"]),
        )
        if playbook_patched:
            payload["writing_intent"] = {
                "enabled": True,
                "source": "writing_playbook",
                "operator": writing_operator,
            }
            from app.services.metrics_service import get_metrics_service

            get_metrics_service().inc_contract_event(f"writing_playbook_{writing_operator}")
        if write_patched:
            payload["thin_execution_profile"] = (
                str(payload.get("thin_execution_profile") or "") or "artifact_edit"
            )
            payload["writing_intent"] = {
                "enabled": True,
                "source": "artifact_edit_patch",
            }
            from app.services.metrics_service import get_metrics_service

            get_metrics_service().inc_contract_event("artifact_edit_write_patched")
        exec_tools, action_tool_params, action_stages = _execution_transport_from_actions(
            actions
        )

        # Legacy selected_tools from the LLM still count (transitional), merged
        # after action-derived tools so actions stay authoritative.
        raw_tools = [str(t) for t in result.get("selected_tools", [])]
        legacy_tools, dropped_tools = normalize_selected_tools(raw_tools)
        for tool in legacy_tools:
            if tool not in exec_tools:
                exec_tools.append(tool)
                action_stages.append([tool])

        tool_params = dict(payload.get("tool_params") or {})
        plan_tool_params = result.get("tool_params")
        if isinstance(plan_tool_params, dict):
            for key, value in plan_tool_params.items():
                if isinstance(value, dict):
                    tool_params[key] = {**(tool_params.get(key) or {}), **value}
                else:
                    tool_params[key] = value
        for key, value in action_tool_params.items():
            tool_params[key] = {**(tool_params.get(key) or {}), **value}

        from app.services.tool_selection import validate_tool_selection

        user_role = str(payload.get("user_role") or "user")
        valid_tools, selection_issues = validate_tool_selection(
            exec_tools,
            tool_params,
            get_tool_registry(),
            user_role,
        )
        if selection_issues:
            dropped_tools = list(dict.fromkeys(list(dropped_tools or []) + selection_issues))
        exec_tools = valid_tools
        action_stages = [[t for t in stage if t in exec_tools] for stage in action_stages]
        action_stages = [stage for stage in action_stages if stage]

        if not plan and actions:
            plan = [
                (a.rationale or a.type)[:60] for a in actions[:8]
            ]

        existing_intent = dict(payload.get("writing_intent") or {})
        if existing_intent.get("enabled"):
            payload["writing_intent"] = existing_intent
        else:
            from app.services.writing_context import resolve_writing_intent_for_plan

            payload["writing_intent"] = resolve_writing_intent_for_plan(
                payload=payload,
                actions=actions,
                goal=goal_for_planning,
            )
        payload["tool_params"] = tool_params
        if action_stages:
            payload["tool_stages"] = action_stages
        tool_dag = result.get("tool_dag")
        if isinstance(tool_dag, dict) and tool_dag.get("nodes"):
            payload["tool_dag"] = tool_dag

        has_retrieve = any(a.type == "retrieve" for a in actions)
        skip_retrieval = bool(result.get("skip_retrieval", not has_retrieve))
        if has_retrieve:
            skip_retrieval = False
        from app.services.writing_context import should_force_writing_retrieval

        if should_force_writing_retrieval(payload):
            skip_retrieval = False
        review_required = str(result.get("risk_level", "LOW")).upper() in (
            "HIGH",
            "CRITICAL",
        )

        report_plan_trace(
            plan,
            exec_tools,
            dropped=dropped_tools or None,
            meta={
                "风险等级": result.get("risk_level"),
                "跳过检索": skip_retrieval,
                "actions": [a.type for a in actions],
                "action_issues": action_issues or "",
            },
        )

        from app.services.intent_composer import bump_intent_revision

        payload = bump_intent_revision(payload)

        from app.services.retrieval_routing import attach_retrieval_context

        retrieval_ctx = attach_retrieval_context(
            {
                **state,
                "input_payload": payload,
                "selected_tools": exec_tools,
                "skip_retrieval": skip_retrieval,
            }
        )
        updated = budget_ctx.apply_to_state(
            merge_state(
                state,
                input_payload=payload,
                plan=plan,
                planned_actions=[a.to_dict() for a in actions] or None,
                selected_tools=exec_tools,
                skip_retrieval=skip_retrieval,
                retrieval_decision=retrieval_ctx.get("retrieval_decision"),
                query_object=retrieval_ctx.get("query_object"),
                task_drift=retrieval_ctx.get("task_drift"),
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
                        "actions": [a.type for a in actions],
                        "action_issues": action_issues,
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
                    "actions": [a.type for a in actions],
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

        max_revisions = 1
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
                    planned_actions=None,
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

        if validation.should_replan and revisions >= max_revisions:
            from app.services.planning_retry_signals import degrade_to_writing_playbook_state

            degraded = degrade_to_writing_playbook_state(updated)
            if degraded is not None:
                from app.services.route_audit.pipeline import run_route_audit_pipeline

                degraded = run_route_audit_pipeline(degraded)
                get_state_store().save(degraded)
                return degraded

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
        from app.services.llm_client import RetryableError
        from app.services.writing_intent_classifier import classify_writing_operator
        from app.services.writing_playbook import apply_writing_playbook
        from app.services.writing_project import writing_project_manifest_exists

        payload_fb = dict(state.get("input_payload") or {})
        goal_fb = str(
            payload_fb.get("goal") or payload_fb.get("query") or ""
        ).strip()
        operator = classify_writing_operator(goal_fb, state)
        if isinstance(exc, RetryableError) and operator and writing_project_manifest_exists(
            str(state["task_id"])
        ):
            actions, plan, patched = apply_writing_playbook(
                [],
                operator=operator,
                goal=goal_fb,
                task_id=str(state["task_id"]),
                state=state,
            )
            if actions and patched:
                payload_fb["writing_operator"] = operator
                payload_fb["writing_intent"] = {
                    "enabled": True,
                    "source": "planning_timeout_playbook",
                }
                exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
                payload_fb["tool_params"] = {**payload_fb.get("tool_params", {}), **tool_params}
                payload_fb["tool_stages"] = stages
                return _finish_thin(
                    state,
                    payload=payload_fb,
                    plan=plan,
                    tools=exec_tools,
                    planned_actions=actions,
                    audit_action="planning_timeout_playbook",
                    audit_detail={"operator": operator, "error": str(exc)[:200]},
                    skip_retrieval=False,
                )
        if isinstance(exc, RetryableError):
            payload_fb["writing_intent"] = payload_fb.get("writing_intent") or {
                "enabled": False,
            }
            return _finish_thin(
                state,
                payload=payload_fb,
                plan=["answer user directly"],
                tools=[],
                planned_actions=[
                    Action(type="answer", completes_turn=True, source="structural")
                ],
                audit_action="planning_timeout_answer",
                audit_detail={"error": str(exc)[:200]},
            )
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"planning: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="planning",
            audit_log=append_audit(state, "planning", "error", {"detail": str(exc)}),
        )
