"""Parallel output guard during answer streaming (先验后流 / 流之中扫描)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from app.config.settings import settings
from app.services.output_guard import scan_pii


@dataclass
class _StreamGuardState:
    blocked: bool = False
    issues: list[str] = field(default_factory=list)
    scanned_len: int = 0


_local = threading.local()


def _state() -> _StreamGuardState:
    if not hasattr(_local, "guard"):
        _local.guard = _StreamGuardState()
    return _local.guard


def reset_stream_output_guard() -> None:
    _local.guard = _StreamGuardState()


def is_stream_output_blocked() -> bool:
    return _state().blocked


def stream_guard_issues() -> list[str]:
    return list(_state().issues)


def consume_stream_guard_result() -> Optional[dict[str, object]]:
    """Return guard payload when stream scan blocked delivery; else None."""
    st = _state()
    if not st.blocked:
        return None
    return {
        "passed": False,
        "issues": list(st.issues),
        "source": "stream_scan",
        "stream_truncated": True,
    }


def scan_summary_before_emit(summary_text: str) -> Optional[list[str]]:
    """
    Incremental PII scan on extracted summary during streaming.
    Returns issues when output must be truncated; None to continue.
    """
    if not getattr(settings, "OUTPUT_GUARD_ENABLED", True):
        return None
    st = _state()
    if st.blocked:
        return st.issues
    text = str(summary_text or "")
    if len(text) <= st.scanned_len:
        return None
    st.scanned_len = len(text)
    prose_only = getattr(settings, "OUTPUT_GUARD_PII_PROSE_ONLY", True)
    issues = scan_pii(text, prose_only=prose_only)
    if issues:
        st.blocked = True
        st.issues = issues
        return issues
    return None
