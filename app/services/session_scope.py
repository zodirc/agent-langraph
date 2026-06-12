"""Session-scoped retrieval context (mirrors tenant_context for knowledge isolation)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_session_scope: ContextVar[Optional[str]] = ContextVar("retrieval_session_id", default=None)


def set_retrieval_session_id(session_id: Optional[str]) -> None:
    _session_scope.set(session_id or None)


def get_retrieval_session_id() -> Optional[str]:
    return _session_scope.get()
