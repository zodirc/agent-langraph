"""Server-composed UI hints for Web/API clients (unified-core WP-6: generic pause/steer only).

Clients render `system_lines` and structured `display` instead of branching on
server internals. All mission/orchestration/steer-confirmation display logic
was removed with the mission runtime.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState


def _control_block(state: AgentState) -> dict[str, Any]:
    ctx = state.get("interrupt_context")
    return ctx if isinstance(ctx, dict) else {}


def _pause_context_lines(state: AgentState) -> list[str]:
    control = _control_block(state)
    lines: list[str] = []
    pause_reason = str(control.get("pause_reason") or "").strip()
    if pause_reason == "user_requested_pause":
        lines.append("任务已按你的请求暂停；已提交内容已保存，发送「继续」可从检查点续跑。")
    elif pause_reason == "user_requested_cancel":
        lines.append("任务已取消；已提交内容保留，未提交部分已丢弃。")
    reason = str(control.get("reason") or "").strip()
    if reason and reason not in lines:
        lines.append(reason)
    return lines


def build_mission_paused_payload(
    state: AgentState,
    *,
    autonomous: bool = False,
    steer_pause: bool = False,
    confirmation_actions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """SSE paused-event body — render system_lines on the client."""
    system_lines = _pause_context_lines(state) or ["task_paused"]
    return {
        "task_id": state["task_id"],
        "status": state.get("status"),
        "autonomous": autonomous,
        "steer_pause": steer_pause,
        "confirmation_actions": confirmation_actions,
        "display": {
            "pause_kind": "user_control"
            if str(_control_block(state).get("pause_reason") or "")
            in ("user_requested_pause", "user_requested_cancel")
            else ("steer" if steer_pause else "stepwise"),
        },
        "system_lines": system_lines,
        "autonomous_ui": {"enabled": False, "behavior": None, "system_lines": []},
    }


def _pending_message_preview(pending: dict[str, Any]) -> Optional[str]:
    entries = pending.get("messages") if isinstance(pending, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    last = entries[-1]
    if isinstance(last, dict):
        return str(last.get("message") or "").strip()[:200] or None
    return str(last).strip()[:200] or None


def build_steer_task_client_display(state: AgentState, *, queued: bool) -> dict[str, Any]:
    pending = state.get("pending_user_message") or {}
    depth = len((pending.get("messages") if isinstance(pending, dict) else []) or [])
    preview = _pending_message_preview(pending if isinstance(pending, dict) else {})

    if queued:
        lines = [
            "steer_queued: will apply after the current step completes",
            f"queue_depth={depth}",
        ]
        if preview:
            lines.append(f"queued_goal: {preview}")
    else:
        lines = ["steer_applied: 消息已写入任务状态"]
        lines.extend(_pause_context_lines(state))

    return {
        "kind": "steer_queued" if queued else "steer_applied",
        "system_lines": lines,
        "supersede_stream_recommended": False,
        "display": {
            "queued": queued,
            "supersede_stream_recommended": False,
            "queue_depth": depth,
            "status": state.get("status"),
        },
    }


def confirmation_panel_display(phase: str) -> dict[str, str]:
    titles = {
        "intent": "steer_intent_confirmation",
        "outcome": "steer_outcome_confirmation",
    }
    return {"phase": phase, "title": titles.get(phase, "steer_confirmation")}
