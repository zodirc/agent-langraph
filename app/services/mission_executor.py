"""
Mission act 执行器：由 mission_act_node 调用（非 LangGraph 节点）。

execute_mission_step 按 step_decision 与 work_item 分发；
run_pipeline_request 内联 planning→retrieval→tool→writing→reasoning（不进 policy）。

Executors for mission_act_node; execute_mission_step and run_pipeline_request drive inline subgraph.
"""

from __future__ import annotations

from typing import Any, Optional

from app.nodes.planning_node import planning_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.nodes.writing_node import writing_node
from app.services.mission_service import prepare_state_for_mission_act, update_progress_from_observation
from app.services.observation import attach_observation
from app.services.state_store import get_state_store
from app.runtime.router import (
    route_after_planning,
    route_after_retrieval,
    route_after_tool,
    route_after_writing,
)
from app.runtime.state import AgentState, merge_state


def _forced_stop_requested(state: AgentState) -> bool:
    from app.services.mission_steer import pending_has_forced_action

    task_id = str(state.get("task_id") or "")
    if not task_id:
        return False
    stored = get_state_store().load(task_id, read_only=True) or {}
    pending = stored.get("pending_user_message")
    if pending_has_forced_action(pending, "pause"):
        return True
    payload = {
        **(stored.get("input_payload") or {}),
        **(state.get("input_payload") or {}),
    }
    intervention = payload.get("mission_intervention") or {}
    return bool(
        str(intervention.get("action") or "") == "pause" and intervention.get("force")
    )


def _bootstrap_executor_routing(state: AgentState) -> AgentState:
    """Attach agenda head + writing_intent so routers run executor, not narrator-only."""
    from app.services.mission_orchestrator import (
        activate_work_item,
        ensure_work_plan,
        get_current_work_item,
        orchestration_enabled,
    )
    from app.services.mission_schema import resolve_writing_intent_for_step
    from app.services.turn_kind import plan_steps_for_display

    mission = state.get("mission") or {}
    if orchestration_enabled(mission):
        state = ensure_work_plan(state)

    item = get_current_work_item(state)
    if not item:
        return merge_state(state, plan=plan_steps_for_display(state))

    state = activate_work_item(state, item)
    payload = dict(state.get("input_payload") or {})
    payload["current_work_item"] = item
    kind = str(item.get("kind") or "")
    write_kinds = frozenset(
        {
            "append_body",
            "append_chapter",
            "write_body",
            "write_outline",
            "reset_body",
            "review_chapter",
            "polish_chapter",
            "chapter_summary",
            "consistency_check",
        }
    )
    if kind in write_kinds:
        resolved = resolve_writing_intent_for_step(
            merge_state(state, input_payload=payload),
            mission=mission,
        )
        if isinstance(resolved, dict):
            payload["writing_intent"] = {
                **resolved,
                "enabled": True,
                "source": "work_item",
            }
    display_plan = plan_steps_for_display(merge_state(state, input_payload=payload))
    return merge_state(state, input_payload=payload, plan=display_plan)


def _run_executor_subgraph(state: AgentState) -> AgentState:
    """Run writing/tools for current agenda item instead of terminating in reasoning."""
    state = _bootstrap_executor_routing(state)
    payload = state.get("input_payload") or {}
    item = payload.get("current_work_item") or {}
    kind = str(item.get("kind") or "")
    if kind in (
        "review_chapter",
        "polish_chapter",
        "chapter_summary",
        "consistency_check",
        "append_body",
        "append_chapter",
        "write_body",
        "write_outline",
        "reset_body",
    ):
        return run_subgraph_writing(state)
    return _run_pipeline_node_loop(state, allow_reasoning_terminal=False)


def _run_pipeline_node_loop(
    state: AgentState,
    *,
    allow_reasoning_terminal: bool,
) -> AgentState:
    from app.runtime.state import TaskStatus
    from app.services.turn_kind import agenda_has_executor_pending, should_use_reasoning_terminal

    current = state
    node = route_after_planning(current)
    if not allow_reasoning_terminal and node == "reasoning" and agenda_has_executor_pending(
        current
    ):
        return _run_executor_subgraph(current)

    safety = 0
    while safety < 12:
        if _forced_stop_requested(current):
            return merge_state(current, status="MISSION_PAUSED", current_node="mission_act")
        safety += 1
        if node == "retrieval":
            current = retrieval_node(current)
            node = route_after_retrieval(current)
        elif node == "tool_execution":
            current = tool_execution_node(current)
            if str(current.get("status", "")).endswith("FAILED"):
                break
            node = route_after_tool(current)
        elif node == "writing":
            current = writing_node(current)
            if str(current.get("status", "")).endswith("FAILED"):
                break
            node = route_after_writing(current)
        elif node == "reasoning":
            from app.services.turn_contract import (
                contract_requires_side_effects,
                is_turn_contract_fulfilled,
                validate_turn_contract_execution,
            )
            from app.services.turn_contract_lifecycle import (
                REASON_INCONSISTENT,
                invalidate_turn_contract_payload,
            )

            payload_check = current.get("input_payload") or {}
            if (
                not allow_reasoning_terminal
                and not should_use_reasoning_terminal(current)
                and agenda_has_executor_pending(current)
            ):
                return _run_executor_subgraph(current)
            if contract_requires_side_effects(
                payload_check, state=current
            ) and not is_turn_contract_fulfilled(current):
                issues = validate_turn_contract_execution(current)
                payload_check = invalidate_turn_contract_payload(
                    payload_check, REASON_INCONSISTENT
                )
                payload_check["plan_validation_feedback"] = {
                    "issues": issues or ["contract_unfulfilled"],
                    "suggestions": ["replan with executable tools or writing phase"],
                }
                current = merge_state(
                    current,
                    input_payload=payload_check,
                    status=TaskStatus.PLANNED.value,
                    current_node="mission_act",
                )
                break
            current = reasoning_node(current)
            break
        elif node == "planning":
            current = planning_node(current)
            node = route_after_planning(current)
        elif node == "dead_letter":
            break
        else:
            break

    return current


def _oma_pipeline_redirect(state: AgentState) -> AgentState:
    """Route legacy pipeline callers to OMAW workers (ADR §7.2)."""
    from app.services.mission_steer import mission_must_run_planning

    state = prepare_state_for_mission_act(state)
    if mission_must_run_planning(state):
        from app.services.mission_oma.planner_worker import run_planner_worker

        return run_planner_worker(state)
    from app.services.mission_oma.orchestrator import run_parallel_reviews_if_applicable

    parallel = run_parallel_reviews_if_applicable(state)
    if parallel is not None:
        return parallel
    from app.services.mission_oma.workers import execute_oma_worker

    return execute_oma_worker(state)


def _oma_act_available(state: AgentState) -> bool:
    from app.services.mission_oma.orchestrator import should_use_mission_oma

    mission = state.get("mission") or {}
    if str(mission.get("kind", "")).lower() == "single_turn":
        return False
    return should_use_mission_oma(state)


def _mission_act_for_writing(
    state: AgentState,
    step_decision: dict,
    *,
    allow_pipeline: bool = False,
) -> AgentState:
    """ADR 7.2: OMAW missions must not use run_pipeline_request as default."""
    if _oma_act_available(state):
        routed = _dispatch_oma_act(state, step_decision)
        if routed is not None:
            return routed
        return run_subgraph_writing(state)
    if allow_pipeline:
        return run_pipeline_request(state)
    return run_subgraph_writing(state)


def _dispatch_oma_act(state: AgentState, step_decision: dict) -> AgentState | None:
    """OMAW dispatch for mission_act — returns None when OMAW is not active."""
    if not _oma_act_available(state):
        return None

    from app.services.mission_oma.orchestrator import (
        narrow_replan_after_acceptance_fail,
        run_parallel_reviews_if_applicable,
    )
    from app.services.mission_oma.planner_worker import run_planner_worker
    from app.services.turn_kind import resolve_turn_kind

    executor = str(step_decision.get("next_executor") or "")
    if executor == "oma:planner":
        return run_planner_worker(state)

    params = dict(step_decision.get("params") or {})
    if (
        str(params.get("writing_phase") or "") == "steer_replan"
        or resolve_turn_kind(state) == "steer_replan"
    ):
        return run_planner_worker(state)

    parallel = run_parallel_reviews_if_applicable(state)
    if parallel is not None:
        return parallel

    payload = state.get("input_payload") or {}
    if payload.get("acceptance_replan") and not payload.get("acceptance_ok", True):
        return narrow_replan_after_acceptance_fail(state)

    return run_subgraph_writing(state)


def run_pipeline_request(state: AgentState) -> AgentState:
    """
    单步内联主图节点与 router，不经 LangGraph compile。

    After planning: steer confirm → executor subgraph; reasoning only when narrate-only.

    ADR-001 §7.2: 不得作为手稿 mission_oma 的默认执行器；OMAW 写作须走 execute_oma_worker。
    """
    from app.services.mission_oma.orchestrator import should_use_mission_oma

    if should_use_mission_oma(state):
        return _oma_pipeline_redirect(state)

    mission = state.get("mission") or {}
    if str(mission.get("kind", "")).lower() == "writing":
        return run_subgraph_writing(state)

    from app.runtime.state import TaskStatus
    from app.services.mission_execution import build_mission_checkpoint_summary
    from app.services.mission_steer_confirm import (
        attach_steer_confirmation_to_state,
        steer_confirmation_pending,
    )
    from app.services.turn_kind import pipeline_phase_after_planning

    state = prepare_state_for_mission_act(state)
    if _forced_stop_requested(state):
        return merge_state(state, status="MISSION_PAUSED", current_node="mission_act")
    current = planning_node(state)
    payload = current.get("input_payload") or {}
    if steer_confirmation_pending(payload):
        return attach_steer_confirmation_to_state(current)

    phase = pipeline_phase_after_planning(current)
    if phase == "await_confirm":
        return attach_steer_confirmation_to_state(current)
    if phase == "steer_replan_only":
        checkpoint = build_mission_checkpoint_summary(current)
        return merge_state(
            current,
            status=TaskStatus.MISSION_PAUSED.value,
            current_node="mission_act",
            reasoning_result={
                "summary": str(checkpoint.get("summary") or "等待 steer 规划确认"),
                "confidence": 0.85,
                "risk_level": "LOW",
                "structured": {"source": "steer_replan_only"},
            },
        )
    if phase == "execute":
        current = _bootstrap_executor_routing(current)
        return _run_pipeline_node_loop(current, allow_reasoning_terminal=False)
    return _run_pipeline_node_loop(current, allow_reasoning_terminal=True)


def _chapter_index_mismatch_note(
    *,
    chapter_cursor: Any,
    outcome: dict[str, Any],
) -> Optional[str]:
    import re

    cursor_n: Optional[int] = None
    try:
        if chapter_cursor is not None:
            cursor_n = int(chapter_cursor)
    except (TypeError, ValueError):
        cursor_n = None
    text = " ".join(
        str(outcome.get(key) or "")
        for key in ("chapter_header", "chapter_summary", "ending_state")
    )
    match = re.search(r"第\s*(\d+)\s*章", text)
    if not match or cursor_n is None:
        return None
    try:
        header_n = int(match.group(1))
    except ValueError:
        return None
    if header_n != cursor_n:
        return f"章号提示：正文标题为第{header_n}章，进度指针为第{cursor_n}章"
    return None


def _mission_writing_reasoning_summary(state: AgentState) -> AgentState:
    """Deterministic post-write summary — file on disk is source of truth (no LLM regen)."""
    from app.runtime.state import TaskStatus, append_audit, merge_state
    from app.services.artifact_tools import is_text_artifact_filename, read_artifact_tail

    obs = state.get("observation") or {}
    manuscript = state.get("manuscript") or obs.get("manuscript") or {}
    metrics = obs.get("progress_metrics") or (state.get("progress") or {}).get("metrics") or {}
    payload = state.get("input_payload") or {}
    outcome = payload.get("last_chapter_outcome") or {}
    intent = payload.get("writing_intent") or {}
    action = str(intent.get("action") or "")
    task_id = str(state["task_id"])

    if action in ("write_outline", "rewrite_outline"):
        path = str(manuscript.get("outline_path") or payload.get("outline_filename") or "outline.txt")
        written = int(manuscript.get("outline_bytes") or 0)
        label = "大纲"
    else:
        path = str(manuscript.get("body_path") or payload.get("novel_filename") or "novel.txt")
        written = int(metrics.get("written_chars") or manuscript.get("body_bytes") or 0)
        label = "正文"

    pct = metrics.get("progress_pct", 0)
    chapter = manuscript.get("chapter_cursor") or metrics.get("chapter_cursor")
    parts = [f"【已写入会话文件】`{path}`（{label}，{written} 字节）。"]
    if path and is_text_artifact_filename(path) and written > 0:
        preview = read_artifact_tail(task_id, path, max_chars=280).strip()
        if preview:
            parts.append(f"预览：{preview[:280]}{'…' if len(preview) >= 280 else ''}")
    if pct:
        parts.append(f"进度 {pct}%")
    if chapter:
        parts.append(f"第 {chapter} 章")
    mismatch = _chapter_index_mismatch_note(chapter_cursor=chapter, outcome=outcome)
    if mismatch:
        parts.append(mismatch)
    if outcome.get("chapter_summary"):
        parts.append(f"本章：{str(outcome['chapter_summary'])[:120]}")
    elif outcome.get("ending_state"):
        parts.append(f"章末：{str(outcome['ending_state'])[:80]}")
    quality = metrics.get("chapter_quality") or outcome.get("quality_rubric")
    if quality and quality.get("composite_score") is not None:
        gate = "通过" if quality.get("pass_gate") else "待校正"
        parts.append(f"质量 {quality['composite_score']:.2f}（{gate}）")
    summary = "，".join(parts) + "。"
    reasoning_result = {
        "summary": summary,
        "confidence": 0.92,
        "risk_level": "LOW",
        "structured": {"source": "mission_writing_skip"},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        status=TaskStatus.REASONED.value,
        current_node="reasoning",
        audit_log=append_audit(
            state,
            "reasoning",
            "success",
            {"confidence": 0.92, "source": "mission_writing_skip"},
        ),
    )


def _run_contract_tool_step(state: AgentState) -> AgentState | None:
    """Execute turn_contract tools without re-entering full planning."""
    from app.services.turn_contract import contract_blocks_writing, contract_tool_names

    payload = state.get("input_payload") or {}
    if not contract_blocks_writing(payload):
        return None
    tools = contract_tool_names(payload)
    if not tools:
        return None
    return tool_execution_node(merge_state(state, selected_tools=tools))


def run_subgraph_writing(state: AgentState) -> AgentState:
    """Writing-focused step: mission step_policy → writing → lightweight reasoning."""
    from app.services.turn_contract import contract_blocks_writing

    state = prepare_state_for_mission_act(state)
    from app.services.mission_oma.orchestrator import (
        run_parallel_reviews_if_applicable,
        should_use_mission_oma,
    )

    if should_use_mission_oma(state):
        parallel = run_parallel_reviews_if_applicable(state)
        if parallel is not None:
            return parallel
        from app.services.mission_oma.workers import execute_oma_worker

        return execute_oma_worker(state)
    from app.services.turn_contract_lifecycle import reconcile_turn_contract_execution

    state = reconcile_turn_contract_execution(state)
    from app.services.mission_steer import mission_must_run_planning

    if mission_must_run_planning(state):
        return run_pipeline_request(state)
    if _forced_stop_requested(state):
        return merge_state(state, status="MISSION_PAUSED", current_node="mission_act")
    payload = dict(state.get("input_payload") or {})
    if contract_blocks_writing(payload):
        tool_step = _run_contract_tool_step(state)
        if tool_step is not None:
            return tool_step
        return run_pipeline_request(state)

    intent = payload.get("writing_intent") or {}
    if not intent.get("enabled"):
        item = payload.get("current_work_item") or {}
        kind = str(item.get("kind") or "")
        write_kinds = frozenset(
            {
                "append_body",
                "write_body",
                "write_outline",
                "reset_body",
                "review_chapter",
                "polish_chapter",
                "chapter_summary",
            }
        )
        if kind not in write_kinds:
            return run_pipeline_request(state)
        mission = state.get("mission") or {}
        from app.services.mission_schema import resolve_writing_intent_for_step

        resolved = resolve_writing_intent_for_step(state, mission=mission)
        if not resolved.get("enabled"):
            return run_pipeline_request(state)
        payload["writing_intent"] = {**resolved, "enabled": True, "source": "work_item"}
        state = merge_state(state, input_payload=payload)

    from app.services.retrieval_policy import skip_knowledge_retrieval

    if not state.get("skip_retrieval") and not skip_knowledge_retrieval(state):
        state = retrieval_node(state)

    current = writing_node(state)
    if str(current.get("status", "")).endswith("FAILED"):
        return current

    current = attach_observation(current)
    current = update_progress_from_observation(current)

    payload = current.get("input_payload") or {}
    from app.services.writing_delivery import writing_persisted_on_state

    if str(current.get("status", "")) == "WRITTEN" and writing_persisted_on_state(current):
        return _mission_writing_reasoning_summary(current)
    command = (payload.get("writing_command") or {})
    if payload.get("force_slow_reasoning") or (
        isinstance(command, dict) and command.get("action") in ("edit_plot", "reset_body", "write_outline")
    ):
        return reasoning_node(current)
    if str(current.get("status", "")) == "WRITTEN":
        return _mission_writing_reasoning_summary(current)
    return reasoning_node(current)


def execute_mission_step(state: AgentState, step_decision: dict) -> AgentState:
    """
    mission_act 调度入口，按 step_decision 与 current_work_item 分发。

    Dispatch act phase; may return MISSION_RUNNING, REASONED, MISSION_PAUSED, etc.
    """
    from app.runtime.state import AgentState, TaskStatus, merge_state
    from app.services.manuscript_service import resolve_manuscript

    from app.services.mission_steer import mission_must_run_planning

    from app.services.mission_steer import review_outline_requested
    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending

    payload = state.get("input_payload") or {}
    if steer_confirmation_pending(payload) and not payload.get("steer_intent_confirmed"):
        return merge_state(
            state,
            current_node="mission_act",
            status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
        )
    if steer_outcome_confirmation_pending(payload) and not payload.get("steer_outcome_confirmed"):
        from app.services.mission_steer_outcome_confirm import (
            attach_steer_outcome_confirmation_to_state,
        )

        return attach_steer_outcome_confirmation_to_state(
            merge_state(
                state,
                current_node="mission_act",
                status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
            )
        )
    if mission_must_run_planning(state):
        payload_plan = dict(state.get("input_payload") or {})
        payload_plan.pop("steer_replan_resume", None)
        payload_plan.pop("foreground_replan_dispatch", None)
        state = merge_state(state, input_payload=payload_plan)
        if _oma_act_available(state):
            from app.services.mission_oma.planner_worker import run_planner_worker

            state = run_planner_worker(state)
        else:
            state = run_pipeline_request(state)
        payload = dict(state.get("input_payload") or {})
        if steer_confirmation_pending(payload) and not payload.get("steer_intent_confirmed"):
            return merge_state(
                state,
                current_node="mission_act",
                status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
            )
        from app.services.turn_kind import agenda_has_executor_pending

        status = str(state.get("status") or "")
        if (
            agenda_has_executor_pending(state)
            and not mission_must_run_planning(state)
            and status
            not in (
                TaskStatus.WRITTEN.value,
                TaskStatus.TOOL_EXECUTED.value,
                TaskStatus.REASONED.value,
            )
        ):
            if _oma_act_available(state):
                return run_subgraph_writing(_bootstrap_executor_routing(state))
            return _run_executor_subgraph(_bootstrap_executor_routing(state))
        return state
    item = payload.get("current_work_item") or {}
    kind = str(item.get("kind") or "")

    if kind == "human_gate":
        return merge_state(
            state,
            tool_results=[],
            status=TaskStatus.MISSION_RUNNING.value,
        )

    if kind == "run_tools":
        return tool_execution_node(state)

    from app.services.writing.state_machine import COMMAND_WORK_ITEM_KINDS

    if kind in COMMAND_WORK_ITEM_KINDS or (
        review_outline_requested(payload) and (payload.get("writing_command") or kind == "review_outline")
    ):
        from app.services.writing.command_builder import build_writing_command
        from app.services.writing.executor import block_command_execution, execute_writing_command
        from app.services.writing.command_validator import validate_target_bound

        command = build_writing_command(state, item if item.get("kind") else None)
        target_error = validate_target_bound(command)
        if target_error is not None:
            return block_command_execution(state, target_error)
        return execute_writing_command(
            state,
            command,
            step_decision=step_decision,
            mission_act_for_writing=_mission_act_for_writing,
            oma_act_available=_oma_act_available,
        )

    from app.services.turn_contract import contract_blocks_writing
    from app.services.turn_contract_lifecycle import reconcile_turn_contract_execution

    state = reconcile_turn_contract_execution(state)
    payload = state.get("input_payload") or {}
    if mission_must_run_planning(state):
        return _mission_act_for_writing(state, step_decision, allow_pipeline=not _oma_act_available(state))

    mission = state.get("mission") or {}
    executor = str(step_decision.get("next_executor") or "pipeline:request")
    if str(mission.get("kind", "")).lower() == "writing" and kind not in (
        "edit_plot",
        "human_gate",
    ):
        executor = "subgraph:writing"
    if executor == "subgraph:writing" and contract_blocks_writing(payload):
        tool_step = _run_contract_tool_step(state)
        if tool_step is not None:
            return tool_step
        return _mission_act_for_writing(state, step_decision)
    if executor in ("subgraph:writing", "oma:planner"):
        return _mission_act_for_writing(state, step_decision)
    if executor == "tools_only":
        return tool_execution_node(state)
    return _mission_act_for_writing(state, step_decision, allow_pipeline=not _oma_act_available(state))
