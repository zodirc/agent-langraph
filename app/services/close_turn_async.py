"""Async turn close: memory writeback + eval capture after delivered (non-critical path)."""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.state_store import get_state_store

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _close_turn_sync(state: AgentState) -> AgentState:
    """Run memory writeback + eval capture; failures are logged, not raised."""
    from app.nodes.eval_capture_node import eval_capture_node
    from app.services.conversation_context import write_turn_memories

    task_id = str(state.get("task_id") or "")
    try:
        updated = write_turn_memories(state)
        updated = merge_state(
            updated,
            current_node="memory_writeback",
            audit_log=append_audit(
                updated,
                "memory_writeback",
                "success",
                {"async": True},
            ),
        )
        from app.services.reflect_turn_async import reflect_turn_audit_sync

        updated = reflect_turn_audit_sync(updated)
        updated = eval_capture_node(updated)
        bg = dict(updated.get("background_status") or {})
        bg["turn_closed"] = True
        bg["turn_closed_at"] = _now_iso()
        updated = merge_state(
            updated,
            background_status=bg,
            audit_log=append_audit(
                updated,
                "turn_closed",
                "success",
                {"task_id": task_id},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        logger.exception("close_turn_async failed task_id=%s", task_id)
        try:
            bg = dict(state.get("background_status") or {})
            bg["turn_closed"] = False
            bg["turn_close_error"] = str(exc)
            failed = merge_state(
                state,
                background_status=bg,
                audit_log=append_audit(
                    state,
                    "turn_closed",
                    "error",
                    {"detail": str(exc), "task_id": task_id},
                ),
            )
            get_state_store().save(failed)
        except Exception:
            logger.exception("close_turn_async persist failure task_id=%s", task_id)
        return state


def close_turn_async(state: AgentState) -> None:
    """Fire-and-forget post-delivery cleanup (at-least-once; best-effort)."""
    snapshot: dict[str, Any] = deepcopy(dict(state))

    def _run() -> None:
        _close_turn_sync(snapshot)  # type: ignore[arg-type]

    threading.Thread(target=_run, daemon=True, name=f"close-turn-{state.get('task_id')}").start()
