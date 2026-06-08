"""Foreground ACK payload builder (optimization WP-1.2, §7.1 L1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_ACK_INTENT_LABELS: dict[str, str] = {
    "new_task": "新任务",
    "clarification": "补充约束",
    "interrupt": "打断当前执行",
    "redirect": "调整目标方向",
    "confirm": "确认继续",
    "reject": "否决当前方案",
    "resume": "恢复任务",
    "status_query": "查询执行状态",
}

_NEXT_STEP: dict[str, str] = {
    "new_task": "理解任务并制定计划",
    "clarification": "合并约束并更新计划",
    "interrupt": "停止当前步骤并重规划",
    "redirect": "按新目标重规划",
    "confirm": "继续执行下一步",
    "reject": "重新评估方案",
    "resume": "从检查点恢复执行",
    "status_query": "汇总当前进度并回复",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_foreground_ack(state: dict[str, Any]) -> dict[str, Any]:
    """Build structured ACK for foreground_status and SSE ack events."""
    payload = dict(state.get("input_payload") or {})
    event_type = str(state.get("event_type") or "new_task")
    classification = payload.get("event_classification") if isinstance(
        payload.get("event_classification"), dict
    ) else {}

    goal = str(payload.get("goal") or payload.get("message") or "").strip()
    intent_label = _ACK_INTENT_LABELS.get(event_type, event_type)
    next_step = _NEXT_STEP.get(event_type, "继续处理")

    detected_interrupt = event_type == "interrupt"
    detected_replan = event_type in {"interrupt", "redirect", "clarification", "reject"}

    will_retrieve = bool(
        payload.get("needs_search")
        or payload.get("skip_retrieval") is False
        or event_type in {"new_task", "clarification", "redirect"}
    )
    will_execute = bool(
        payload.get("selected_tools")
        or payload.get("execution_mode") in {"engineering", "mission"}
        or event_type in {"new_task", "resume", "confirm"}
    )

    message = f"已识别：{intent_label}。下一步：{next_step}。"
    if detected_interrupt:
        message = f"已收到打断请求。下一步：{next_step}。"
    elif event_type == "status_query":
        message = "正在查询当前任务状态…"

    return {
        "phase": "ack",
        "event_type": event_type,
        "event_id": state.get("event_id"),
        "recognized_intent": intent_label,
        "goal_preview": goal[:120] if goal else "",
        "will_retrieve": will_retrieve,
        "will_execute_tools": will_execute,
        "detected_interrupt": detected_interrupt,
        "detected_replan": detected_replan,
        "next_step": next_step,
        "message": message,
        "classification_source": classification.get("source"),
        "emitted_at": _now_iso(),
    }
