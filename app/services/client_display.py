"""Clients should render `system_lines` and structured `display`

Server-composed UI hints for Web/API clients.
`autonomous_ui`
instead of branching on intervention action names in the browser."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.mission_intervention import intervention_from_payload
from app.services.mission_steer_confirm import steer_confirmation_pending
from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending


def _orchestration_line(state: AgentState) -> str:
    from app.services.mission_orchestrator import orchestration_summary

    return orchestration_summary(state) or ""


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
    progress = state.get("progress") or {}
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
            "请调大配置后执行 /resume，或新建会话继续写作。"
        )
    from app.services.mission_orchestrator import orchestration_detail

    detail = orchestration_detail(state)
    done = int(detail.get("done") or 0)
    total = int(detail.get("total") or 0)
    if total > 0 and done >= total and not detail.get("current_title"):
        return "编排队列已完成；若仍需续写请发送「继续」或 /resume。"
    if total > 0 and not detail.get("current_title") and done < total:
        return (
            f"编排进度 {done}/{total}，当前无活动工作项；"
            f"发送「继续」或 /resume 以生成下一项"
            + (f"（建议：{action_label}）" if action_label else "")
        )
    if action_label:
        return f"下一步：{action_label}"
    return None


def autonomous_ui_for_pause(state: AgentState, *, autonomous: bool, steer_pause: bool) -> dict[str, Any]:
    payload = _payload_for_gates(state)
    control = state.get("mission_control") or {}
    if not autonomous:
        return {"enabled": False, "behavior": None, "system_lines": []}

    lines: list[str] = []
    behavior: Optional[str] = None
    resume_confirm = False

    if steer_outcome_confirmation_pending(payload):
        behavior = "wait_outcome_confirm"
        lines.append("autonomous: paused until outcome gate approved (confirm:true on resume/steer)")
    elif steer_confirmation_pending(payload):
        behavior = "wait_intent_confirm"
        lines.append("autonomous: paused until intent gate approved (confirm:true on resume/steer)")
    elif payload.get("steer_review_outline"):
        behavior = "steer_review_only"
        lines.append("autonomous: review_outline — resume when ready to read outline")
    elif _wall_clock_pause(control):
        behavior = "wall_clock_pause"
        lines.append(
            "autonomous: wall-clock limit reached — increase max_wall_sec or /resume manually"
        )
    elif _consecutive_failures_paused(state, control):
        behavior = "failure_pause"
        lines.append(
            "autonomous: paused after repeated step failures — steer or resume manually when ready"
        )
    elif steer_pause:
        behavior = "resume_after_steer"
        lines.append("autonomous: steer applied — call resume to continue planning/execution")
    elif not steer_pause:
        behavior = "auto_resume_step"
        lines.append("autonomous: auto-resume next work item")

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
    payload = _payload_for_gates(state)
    control = state.get("mission_control") or {}
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
            "pause_kind": "steer" if steer_pause else "stepwise",
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


def build_steer_task_client_display(state: AgentState, *, queued: bool) -> dict[str, Any]:
    payload = state.get("input_payload") or {}
    pending = state.get("pending_user_message") or {}
    depth = len((pending.get("messages") if isinstance(pending, dict) else []) or [])
    intervention = _intervention_display(payload)

    pause_reason = ""
    mc = state.get("mission_control")
    if isinstance(mc, dict):
        pause_reason = str(mc.get("pause_reason") or "")

    if queued:
        lines = [
            "steer_queued: will apply after the current mission step completes",
            f"queue_depth={depth}",
            "不会自动续跑；当前步结束后消费队列。",
        ]
    else:
        lines = ["steer_applied: 插入/纠偏已写入任务状态"]
        if pause_reason == "worker_lost":
            lines.append(
                "执行器已中断：不会自动写作；续跑请 /resume 或发送「继续写作」。"
            )
        elif str(state.get("status") or "") == "MISSION_PAUSED":
            lines.append("任务已暂停：续跑请 /resume 或发送「继续写作」。")
        if intervention and intervention.get("reason"):
            lines.append(intervention["reason"])
        elif intervention and intervention.get("action"):
            lines.append(f"mission_intervention.action={intervention['action']}")

    return {
        "kind": "steer_queued" if queued else "steer_applied",
        "system_lines": lines,
        "display": {
            "queued": queued,
            "queue_depth": depth,
            "status": state.get("status"),
            "intervention": intervention,
        },
    }


def confirmation_panel_display(phase: str) -> dict[str, str]:
    titles = {
        "intent": "steer_intent_confirmation",
        "outcome": "steer_outcome_confirmation",
    }
    return {"phase": phase, "title": titles.get(phase, "steer_confirmation")}
