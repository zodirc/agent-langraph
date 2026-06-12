"""Turn wall-clock watchdog: finalize hung runs with TIMED_OUT + user message."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TIMED_OUT_MESSAGE = (
    "任务已超时（超过配置的单轮时间上限），已自动终止。"
    "请缩小单次目标或分步继续。"
)

_pending_stream_events: dict[str, dict[str, Any]] = {}


def timed_out_user_message() -> str:
    return _TIMED_OUT_MESSAGE


def finalize_timed_out_task(task_id: str, *, reason: str = "turn_wall_clock_budget_exceeded") -> bool:
    """Mark task TIMED_OUT, set final_answer, request cancel. Returns True if state existed."""
    from app.runtime.state import TaskStatus, append_audit, merge_state
    from app.services.state_store import get_state_store
    from app.services.task_control import request_cancel

    store = get_state_store()
    stored = store.load(task_id)
    if not stored:
        return False

    try:
        request_cancel(task_id, requested_by="turn_watchdog", reason=reason)
    except Exception:
        pass

    msg = _TIMED_OUT_MESSAGE
    ctx = dict(stored.get("interrupt_context") or {})
    ctx["runtime_state"] = "ABORTED"
    ctx["cancel_requested"] = True
    updated = merge_state(
        stored,
        status=TaskStatus.TIMED_OUT.value,
        final_answer=msg,
        current_node="watchdog",
        interrupt_context=ctx,
        audit_log=append_audit(
            stored,
            "watchdog",
            "timed_out",
            {"reason": reason},
        ),
    )
    store.save(updated)
    _pending_stream_events[str(task_id)] = {
        "task_id": task_id,
        "status": TaskStatus.TIMED_OUT.value,
        "final_answer": msg,
        "reason": reason,
    }
    logger.warning("Turn watchdog timed out task %s", task_id)
    return True


def pop_watchdog_stream_event(task_id: str) -> dict[str, Any] | None:
    return _pending_stream_events.pop(str(task_id), None)


def clear_watchdog_events_for_tests() -> None:
    _pending_stream_events.clear()
