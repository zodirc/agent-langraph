"""Reuse one query embedding per retrieval turn (§7.3 scheme B)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_scope_query: ContextVar[Optional[str]] = ContextVar("scope_query_text", default=None)
_scope_vec: ContextVar[Optional[list[float]]] = ContextVar("scope_query_vec", default=None)


def enter_query_embedding_scope(query: str) -> None:
    _scope_query.set((query or "").strip())
    _scope_vec.set(None)


def exit_query_embedding_scope() -> None:
    _scope_query.set(None)
    _scope_vec.set(None)


def get_scoped_embedding(text: str) -> Optional[list[float]]:
    scope = _scope_query.get()
    if not scope or (text or "").strip() != scope:
        return None
    return _scope_vec.get()


def remember_scoped_embedding(text: str, vec: list[float]) -> None:
    scope = _scope_query.get()
    if scope and (text or "").strip() == scope:
        _scope_vec.set(vec)
