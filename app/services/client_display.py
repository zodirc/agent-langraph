"""Clients should render `system_lines` and structured `display`

Server-composed UI hints for Web/API clients.
`autonomous_ui`
instead of branching on intervention action names in the browser."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus
from app.services.mission_intervention import intervention_from_payload
from app.services.mission_steer_confirm import steer_confirmation_pending
from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending


def _orchestration_line(state: AgentState) -> str:
    from app.services.mission_orchestrator import orchestration_summary

    return orchestration_summary(state) or ""


def build_mission_status_answer(state: AgentState) -> str:
    """Factual mission snapshot for status/meta questions (no LLM chapter generation)."""
    from app.services.mission_orchestrator import orchestration_progress_brief
    from app.services.mission_steer import pending_steer_is_set

    payload = state.get("input_payload") or {}
    manuscript = state.get("manuscript") or {}
    status = str(state.get("status") or "unknown")
    node = str(state.get("current_node") or "")
    lines = [
        f"当前任务状态：{status}"
        + (f"（节点 {node}）" if node else ""),
        orchestration_progress_brief(state),
    ]
    outline = manuscript.get("outline_path")
    if outline:
        lines.append(
            f"大纲文件：{outline}（{int(manuscript.get('outline_bytes') or 0)} 字节）"
        )
    body = manuscript.get("body_path")
    if body:
        lines.append(f"正文文件：{body}（{int(manuscript.get('body_bytes') or 0)} 字节）")
    objective = str((state.get("mission") or {}).get("objective") or payload.get("goal") or "")
    if objective:
        lines.append(f"写作目标：{objective[:120]}")
    if pending_steer_is_set(state.get("pending_user_message")):
        lines.append("已收到你的插入消息；将在当前写作步骤结束后暂停处理（不会自动续写下一章）。")
    elif status == TaskStatus.MISSION_RUNNING.value:
        lines.append("Mission 正在执行中；长写作步骤可能仍需数十秒才能安全打断。")
    return "\n".join(lines)


def _turn_contract_display(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    from app.services.turn_contract import contract_from_payload

    contract = contract_from_payload(payload)
    if not contract:
        return None
    return {
        "primary_op": contract.get("primary_op"),
        "intent_kind": contract.get("intent_kind"),
        "forbid": list(contract.get("forbid") or [])[:6],
        "reason": str(contract.get("user_visible_reason") or "").strip() or None,
    }


def _turn_contract_summary_line(payload: dict[str, Any]) -> Optional[str]:
    block = _turn_contract_display(payload)
    if not block:
        return None
    op = str(block.get("primary_op") or "")
    if op == "batch_unit_quality":
        return "本回合计划：按已写章节逐章审阅（已暂停自动续写下一章）。"
    if op == "edit_plot":
        return "本回合计划：局部修订大纲（非续写正文）。"
    if op == "review_outline":
        return "本回合计划：检阅大纲。"
    if block.get("reason"):
        return str(block["reason"])[:200]
    return f"本回合计划：{op}" if op else None


def _intervention_display(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    block = intervention_from_payload(payload)
    if not block:
        return None
    return {
        "action": block.get("action"),
        "force": bool(block.get("force")),
        "reason": str(block.get("reason") or "").strip() or None,
    }


def _wall_clock_pause(control: dict[str, Any]) -> bool:
    return "wall_clock" in str(control.get("reason") or "").lower()


def _pause_context_lines(state: AgentState, control: dict[str, Any]) -> list[str]:
    """User-visible lines from LLM/planner fields, not client-side action templates."""
    payload = state.get("input_payload") or {}
    lines: list[str] = []
    intervention = _intervention_display(payload)
    if intervention and intervention.get("reason"):
        lines.append(intervention["reason"])
    elif intervention and intervention.get("action"):
        lines.append(f"mission_intervention.action={intervention['action']}")

    confirm_block = payload.get("steer_intent_confirmation") or {}
    if steer_confirmation_pending(payload) and confirm_block.get("summary_text"):
        first = str(confirm_block["summary_text"]).strip().split("\n")[0]
        if first and first not in lines:
            lines.append(first)

    reason = str(control.get("reason") or "").strip()
    pause_reason = str(control.get("pause_reason") or "").strip()
    if pause_reason == "user_requested_pause":
        lines.append("任务已按你的请求暂停；已提交内容已保存，发送「继续」可从检查点续跑。")
    elif pause_reason == "user_requested_cancel":
        lines.append("任务已取消；已提交内容保留，未提交部分已丢弃。")
    if reason and reason not in lines:
        lines.append(reason)

    orch = _orchestration_line(state)
    if orch:
        lines.append(orch)
    return lines


def _payload_for_gates(state: AgentState) -> dict[str, Any]:
    from app.services.state_store import get_state_store, merge_input_payload_for_gates

    stored = get_state_store().load(state["task_id"], read_only=True)
    return merge_input_payload_for_gates(state, stored or state)


def _consecutive_failures_paused(state: AgentState, control: dict[str, Any]) -> bool:
    if "consecutive_failures" in str(control.get("reason") or ""):
        return True
    from app.runtime.state_field_access import progress_from_state

    progress = progress_from_state(state) or {}
    failures = int(progress.get("consecutive_failures") or 0)
    if failures <= 0:
        return False
    from app.config.settings import settings

    mission = state.get("mission") or {}
    budget = mission.get("budget") or {}
    max_failures = int(
        budget.get("max_failures") or getattr(settings, "MISSION_MAX_FAILURES", 3)
    )
    return failures >= max_failures


def _next_step_line(state: AgentState, control: dict[str, Any], action_label: Optional[str]) -> Optional[str]:
    if _wall_clock_pause(control):
        return (
            "任务已暂停：已达 mission 墙钟上限（max_wall_sec）。"
            "请调大配置后发送「继续」，或新建会话继续写作。"
        )
    from app.services.mission_orchestrator import orchestration_detail

    detail = orchestration_detail(state)
    done = int(detail.get("done") or 0)
    total = int(detail.get("total") or 0)
    if total > 0 and done >= total and not detail.get("current_title"):
        return "编排队列已完成；若仍需续写请发送「继续」。"
    if total > 0 and not detail.get("current_title") and done < total:
        return (
            f"编排进度 {done}/{total}，当前无活动工作项；"
            f"发送「继续」以生成下一项"
            + (f"（建议：{action_label}）" if action_label else "")
        )
    if action_label:
        return f"下一步：{action_label}"
    return None


def autonomous_ui_for_pause(state: AgentState, *, autonomous: bool, steer_pause: bool) -> dict[str, Any]:
    from app.runtime.state_field_access import mission_control_from_state

    payload = _payload_for_gates(state)
    control = mission_control_from_state(state)
    if not autonomous:
        return {"enabled": False, "behavior": None, "system_lines": []}

    lines: list[str] = []
    behavior: Optional[str] = None
    resume_confirm = False

    if steer_outcome_confirmation_pending(payload):
        behavior = "wait_outcome_confirm"
        lines.append("autonomous: paused until outcome gate approved (/confirm or confirm:true)")
    elif steer_confirmation_pending(payload):
        behavior = "wait_intent_confirm"
        lines.append("autonomous: paused until intent gate approved (/confirm or confirm:true)")
    elif payload.get("steer_review_outline"):
        behavior = "steer_review_only"
        lines.append("autonomous: review_outline — resume when ready to read outline")
    elif _wall_clock_pause(control):
        behavior = "wall_clock_pause"
        lines.append(
            "autonomous: wall-clock limit reached — increase max_wall_sec or send 继续"
        )
    elif _consecutive_failures_paused(state, control):
        behavior = "failure_pause"
        lines.append(
            "autonomous: paused after repeated step failures — steer or resume manually when ready"
        )
    elif steer_pause:
        behavior = "manual_resume"
        lines.append("autonomous: paused after steer — send 「继续写作」 when ready")
    elif not steer_pause:
        behavior = "manual_resume"
        lines.append("autonomous: step paused — send 「继续写作」 when ready")

    if behavior in ("wait_outcome_confirm", "wait_intent_confirm"):
        resume_confirm = True

    if behavior in ("failure_pause", "wall_clock_pause"):
        resume_confirm = False

    return {
        "enabled": True,
        "behavior": behavior,
        "resume_confirm": resume_confirm,
        "system_lines": lines,
    }


_WRITING_ACTION_LABELS = {
    "append_body": "继续生成下一章",
    "write_outline": "生成/更新大纲",
    "write_body": "开始正文",
    "review_chapter": "审阅当前章节",
    "polish_chapter": "润色当前章节",
    "chapter_summary": "整理本章摘要",
    "consistency_check": "检查连贯性",
    "reset_body": "重写正文",
    "bridge_chapter": "生成桥接段",
    "patch_recent_chapter": "修补近期章节",
}


def _writing_action_labels() -> dict[str, str]:
    display_cfg = getattr(settings, "DISPLAY_CONFIG", {})
    if not isinstance(display_cfg, dict):
        return _WRITING_ACTION_LABELS
    writing_cfg = display_cfg.get("writing_actions")
    if not isinstance(writing_cfg, dict):
        return _WRITING_ACTION_LABELS
    merged = dict(_WRITING_ACTION_LABELS)
    for k, v in writing_cfg.items():
        ks = str(k).strip()
        vs = str(v).strip()
        if ks and vs:
            merged[ks] = vs
    return merged


def writing_action_label(state: AgentState) -> Optional[str]:
    """User-facing action label (hides Mission runtime vocabulary)."""
    labels = _writing_action_labels()
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    action = str(intent.get("action") or intent.get("writing_phase") or "")
    if action in labels:
        return labels[action]
    alignment = payload.get("outline_body_alignment") or {}
    body_action = alignment.get("body_action")
    if body_action in labels:
        return labels[body_action]
    return None


def build_mission_paused_payload(
    state: AgentState,
    *,
    autonomous: bool,
    steer_pause: bool,
    confirmation_actions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """SSE mission_paused body — render system_lines on the client."""
    from app.runtime.state_field_access import mission_control_from_state

    payload = _payload_for_gates(state)
    control = mission_control_from_state(state)
    intervention = _intervention_display(payload)

    system_lines = _pause_context_lines(state, control)
    if not system_lines:
        system_lines = ["mission_paused"]

    auto_ui = autonomous_ui_for_pause(state, autonomous=autonomous, steer_pause=steer_pause)
    action_label = writing_action_label(state)
    next_line = _next_step_line(state, control, action_label)
    if next_line:
        system_lines = [next_line] + [ln for ln in system_lines if ln != "mission_paused"]

    return {
        "task_id": state["task_id"],
        "status": state.get("status"),
        "autonomous": autonomous,
        "steer_pause": steer_pause,
        "steer_review_only": bool(payload.get("steer_review_outline")),
        "steer_intent_pending_confirm": steer_confirmation_pending(payload),
        "steer_intent_confirmation": payload.get("steer_intent_confirmation"),
        "steer_outcome_pending_confirm": steer_outcome_confirmation_pending(payload),
        "steer_outcome_confirmation": payload.get("steer_outcome_confirmation"),
        "confirmation_actions": confirmation_actions,
        "display": {
            "pause_kind": (
                "user_control"
                if str(control.get("pause_reason") or "") in ("user_requested_pause", "user_requested_cancel")
                else ("steer" if steer_pause else "stepwise")
            ),
            "writing_action": action_label,
            "mission_control": control,
            "intervention": intervention,
            "orchestration_summary": _orchestration_line(state),
        },
        "system_lines": system_lines,
        "autonomous_ui": auto_ui,
        # Legacy flags for older clients (avoid new branching in JS)
        "steer_action": (intervention or {}).get("action"),
        "orchestration": None,
        "orchestration_detail": None,
    }


def _pending_steer_preview(pending: dict[str, Any]) -> Optional[str]:
    entries = pending.get("messages") if isinstance(pending, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    last = entries[-1]
    if isinstance(last, dict):
        return str(last.get("message") or "").strip()[:200] or None
    return str(last).strip()[:200] or None


def build_steer_task_client_display(state: AgentState, *, queued: bool) -> dict[str, Any]:
    from app.services.interaction_goal import goal_is_mission_status_query

    payload = state.get("input_payload") or {}
    pending = state.get("pending_user_message") or {}
    depth = len((pending.get("messages") if isinstance(pending, dict) else []) or [])
    intervention = _intervention_display(payload)
    preview = _pending_steer_preview(pending if isinstance(pending, dict) else {})
    status_inquiry = bool(payload.get("mission_status_inquiry")) or (
        preview and goal_is_mission_status_query(preview)
    )

    pause_reason = ""
    mc = state.get("mission_control")
    if isinstance(mc, dict):
        pause_reason = str(mc.get("pause_reason") or "")

    contract_line = _turn_contract_summary_line(payload)
    pending_contract = _turn_contract_display(payload)

    if queued:
        preempt_active = False
        replanning = False
        ctx = state.get("interrupt_context") or {}
        if isinstance(ctx, dict):
            control = str(ctx.get("control_state") or "")
            preempt_active = control == "INTERRUPT_REQUESTED"
            replanning = control == "REPLANNING"
        lines = [
            "steer_queued: replanning — old plan discarded, awaiting new planning"
            if replanning
            else (
                "steer_queued: foreground preempt active — current generation will stop"
                if preempt_active
                else "steer_queued: will apply after the current mission step completes"
            ),
            f"queue_depth={depth}",
            "不会自动续跑；已废弃旧 step 提交权，将按最新约束重新规划（非 append）。"
            if (preempt_active or replanning)
            else "不会自动续跑；当前步结束后消费队列。",
        ]
        if preview:
            lines.append(f"queued_goal: {preview}")
        elif str((intervention or {}).get("action") or "") == "batch_unit_quality":
            lines.append("queued_intent: 逐章质量审阅（消费后将取消 pending 续写项）")
        elif str((pending_contract or {}).get("primary_op") or "") == "batch_unit_quality":
            lines.append("queued_intent: 逐章质量审阅（消费后将取消 pending 续写项）")
        if contract_line and (preempt_active or replanning) and preview:
            lines.append(f"supersedes_plan: {contract_line}")
        elif contract_line and not preview:
            lines.append(f"queued_intent: {contract_line}")
        if status_inquiry:
            lines.append(build_mission_status_answer(state))
        elif not preview and not contract_line:
            lines.append("续写已暂停直至队列消费；消费后将重新规划（非自动 append 下一章）。")
    else:
        lines = ["steer_applied: 插入/纠偏已写入任务状态"]
        from app.services.session_fsm import get_fsm_state, routing_needs_replan

        replan_pending = routing_needs_replan(state)
        replanning = get_fsm_state(state) == "REPLANNING"
        ctx = state.get("interrupt_context") or {}
        if replan_pending or pause_reason == "foreground_replan" or replanning:
            lines.append(
                "steer_replan_pending: 纠偏已生效，旧计划已作废；将重新规划（非 append 续写）。"
            )
            steer_preview = str(payload.get("latest_steer_message") or preview or "").strip()
            if steer_preview:
                lines.append(f"steer_goal: {steer_preview[:200]}")
            rev = payload.get("intent_revision")
            if rev:
                lines.append(f"intent_revision: {rev}")
            fg = (ctx.get("foreground_operation") or {}) if isinstance(ctx, dict) else {}
            if fg.get("status"):
                lines.append(f"foreground_op: {fg.get('kind')} / {fg.get('status')}")
        if contract_line:
            lines.append(contract_line)
        if pause_reason == "worker_lost":
            lines.append(
                "执行器已中断：不会自动写作；续跑请发送「继续写作」。"
            )
        elif str(state.get("status") or "") == "MISSION_PAUSED":
            if replan_pending or replanning:
                lines.append("纠偏已接管前台：将启动重新规划（发送纠偏消息即可）。")
            else:
                lines.append("任务已暂停：续跑请发送「继续写作」。")
        if status_inquiry:
            lines.append(build_mission_status_answer(state))
        if intervention and intervention.get("reason"):
            lines.append(intervention["reason"])
        elif intervention and intervention.get("action"):
            lines.append(f"mission_intervention.action={intervention['action']}")

    from app.services.mission_supersede import is_supersede_replan_pending

    from app.services.session_fsm import FSM_REPLANNING, get_fsm_state

    supersede_stream_recommended = (
        not queued
        and is_supersede_replan_pending(payload, state)
        and get_fsm_state(state) in (FSM_REPLANNING, "WAITING_USER")
    )

    return {
        "kind": "steer_queued" if queued else "steer_applied",
        "system_lines": lines,
        "supersede_stream_recommended": supersede_stream_recommended,
        "display": {
            "queued": queued,
            "supersede_stream_recommended": supersede_stream_recommended,
            "queue_depth": depth,
            "status": state.get("status"),
            "intervention": intervention,
            "turn_contract": pending_contract,
        },
    }


def confirmation_panel_display(phase: str) -> dict[str, str]:
    titles = {
        "intent": "steer_intent_confirmation",
        "outcome": "steer_outcome_confirmation",
    }
    return {"phase": phase, "title": titles.get(phase, "steer_confirmation")}
