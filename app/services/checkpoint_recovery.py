"""Checkpoint corruption detection and thread reset."""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

_REQUIRED_STATE_KEYS = ("task_id", "status", "session_id")


def detect_corrupt_checkpoint(
    thread_id: str,
    *,
    state: dict[str, Any] | None = None,
    max_node_repeat: int | None = None,
) -> dict[str, Any]:
    """
    Return {"corrupt": bool, "reason": str, "last_valid_step": int | None}.
    """
    repeat_limit = max_node_repeat or settings.CHECKPOINT_MAX_NODE_REPEAT

    if state:
        missing = [k for k in _REQUIRED_STATE_KEYS if not state.get(k)]
        if missing:
            return {
                "corrupt": True,
                "reason": f"missing required fields: {', '.join(missing)}",
                "last_valid_step": None,
            }
        history = state.get("node_history") or []
        if _detect_node_loop(history, repeat_limit):
            return {
                "corrupt": True,
                "reason": f"node loop detected (>{repeat_limit} repeats)",
                "last_valid_step": None,
            }

    if settings.CHECKPOINT_CORRUPTION_DETECTION_ENABLED:
        load_error = _probe_checkpoint_load(thread_id)
        if load_error:
            return {
                "corrupt": True,
                "reason": load_error,
                "last_valid_step": None,
            }

    return {"corrupt": False, "reason": "", "last_valid_step": None}


def _detect_node_loop(history: list[Any], limit: int) -> bool:
    if not history or limit <= 0:
        return False
    nodes = [
        str(item.get("node") if isinstance(item, dict) else item)
        for item in history
    ]
    if len(nodes) < limit:
        return False
    tail = nodes[-limit:]
    if len(set(tail)) == 1:
        return True
    return False


def _probe_checkpoint_load(thread_id: str) -> str | None:
    try:
        from app.runtime.checkpointer import create_checkpointer

        cp = create_checkpointer()
        if not hasattr(cp, "get_tuple"):
            return None
        config = {"configurable": {"thread_id": thread_id}}
        tup = cp.get_tuple(config)
        if tup is None:
            return None
        checkpoint = getattr(tup, "checkpoint", None) or (tup[1] if isinstance(tup, tuple) else None)
        if checkpoint is None:
            return None
        channel_values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
        if channel_values is None and isinstance(checkpoint, dict):
            return None
        if isinstance(channel_values, dict):
            missing = [k for k in _REQUIRED_STATE_KEYS if k not in channel_values]
            if missing:
                return f"checkpoint missing keys: {', '.join(missing)}"
    except Exception as exc:
        return f"checkpoint deserialize failed: {exc}"
    return None


def reset_thread(thread_id: str, *, keep_audit: bool = True, reason: str = "") -> bool:
    """Delete checkpoints for thread_id; optionally log audit event."""
    try:
        from app.runtime.checkpointer import create_checkpointer

        cp = create_checkpointer()
        if hasattr(cp, "delete_thread"):
            cp.delete_thread(thread_id)
        else:
            logger.warning("checkpointer has no delete_thread for %s", thread_id)
            return False

        if keep_audit:
            _audit_checkpoint_reset(thread_id, reason or "manual_reset")

        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_checkpoint_reset(reason or "manual")
        return True
    except Exception as exc:
        logger.exception("reset_thread failed for %s: %s", thread_id, exc)
        return False


def _audit_checkpoint_reset(thread_id: str, reason: str) -> None:
    try:
        from app.services.audit_store import get_audit_store
        from datetime import datetime, timezone

        task_id = thread_id.split(":")[0] if ":" in thread_id else thread_id
        get_audit_store().append_events(
            task_id,
            [
                {
                    "event": "checkpoint_reset",
                    "thread_id": thread_id,
                    "reason": reason,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
    except Exception as exc:
        logger.warning("audit checkpoint_reset failed: %s", exc)


def handle_invoke_failure(
    thread_id: str,
    exc: BaseException,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    On invoke failure, detect corruption and optionally reset thread.
    Returns action dict for caller, or None if not handled.
    """
    message = str(exc).lower()
    looks_corrupt = any(
        token in message
        for token in ("pickle", "deserialize", "checkpoint", "jsondecode", "invalid state")
    )
    diagnosis = detect_corrupt_checkpoint(thread_id, state=state)
    if not looks_corrupt and not diagnosis["corrupt"]:
        return None

    reason = diagnosis["reason"] or str(exc)
    if settings.CHECKPOINT_AUTO_RESET_ON_CORRUPT:
        reset_thread(thread_id, reason=reason)
        return {"status": "checkpoint_reset", "thread_id": thread_id, "reason": reason}

    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_checkpoint_corrupt()
    return {"status": "checkpoint_corrupt", "thread_id": thread_id, "reason": reason}
