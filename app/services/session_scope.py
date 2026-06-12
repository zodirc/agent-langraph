"""Session-scoped retrieval context (mirrors tenant_context for knowledge isolation)."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger(__name__)

_session_scope: ContextVar[Optional[str]] = ContextVar("retrieval_session_id", default=None)


def set_retrieval_session_id(session_id: Optional[str]) -> None:
    sid = session_id or None
    _session_scope.set(sid)
    if sid:
        logger.debug("retrieval_session_id set: %s", sid)


def log_source_recall_miss_if_needed(
    session_id: str,
    *,
    domains: set[str],
    source_hit_count: int,
) -> None:
    """Warn when session has source docs in store but retrieval returned zero hits."""
    if "source" not in domains or source_hit_count > 0:
        return
    try:
        from app.services.knowledge_store import get_knowledge_store
        from app.services.metrics_service import get_metrics_service

        store = get_knowledge_store()
        docs = store.list_documents(limit=300)
        has_source = any(
            isinstance(d, dict)
            and str((d.get("metadata") or {}).get("session_id") or "") == session_id
            and str((d.get("metadata") or {}).get("domain") or "").lower() == "source"
            for d in docs
        )
        if has_source:
            get_metrics_service().inc_contract_event("source_recall_miss")
            logger.warning(
                "source_recall_miss: session %s has domain=source docs but retrieval returned 0 hits",
                session_id,
            )
    except Exception as exc:
        logger.debug("source_recall_miss check skipped: %s", exc)


def get_retrieval_session_id() -> Optional[str]:
    return _session_scope.get()
