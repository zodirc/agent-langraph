"""LangGraph Checkpointer

SQLite or PostgreSQL per CHECKPOINT_BACKEND.
thread_id from graph_thread_id; resume_graph after human_review interrupt.
checkpoint_recovery may reset thread on invoke failure and retry."""

from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

_checkpointer: Any | None = None


def create_checkpointer() -> Any:
    """Return a LangGraph checkpointer; call setup() once via init_checkpointer()."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    if settings.CHECKPOINT_BACKEND == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        from app.services.db import get_postgres_pool

        pool = get_postgres_pool()
        _checkpointer = PostgresSaver(pool)
        logger.info("LangGraph checkpointer: PostgreSQL")
        return _checkpointer

    from langgraph.checkpoint.sqlite import SqliteSaver

    db_path = Path(settings.CHECKPOINT_SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _checkpointer = SqliteSaver(conn)
    logger.info("LangGraph checkpointer: SQLite (%s)", db_path)
    return _checkpointer


def init_checkpointer() -> None:
    """Create DB tables for the active checkpointer backend."""
    cp = create_checkpointer()
    if hasattr(cp, "setup"):
        cp.setup()


def reset_checkpointer() -> None:
    """Clear cached checkpointer (tests / shutdown)."""
    global _checkpointer
    _checkpointer = None


@lru_cache(maxsize=1)
def checkpoint_label() -> str:
    return settings.CHECKPOINT_BACKEND
