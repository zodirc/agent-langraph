"""Initialize storage backends at application startup."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config.settings import settings
from app.runtime.checkpointer import init_checkpointer, reset_checkpointer
from app.services.db import close_postgres_pool, init_postgres_schema, uses_postgres

logger = logging.getLogger(__name__)


def init_storage() -> None:
    Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.VECTORSTORE_PATH).mkdir(parents=True, exist_ok=True)

    if settings.STORAGE_BACKEND == "postgres":
        if not settings.POSTGRES_URL:
            raise RuntimeError("STORAGE_BACKEND=postgres requires DATABASE_URL or storage.postgres_url")
        init_postgres_schema()
        logger.info("Business storage: PostgreSQL")
    else:
        logger.info("Business storage: SQLite (%s)", settings.SQLITE_PATH)

    init_checkpointer()


def shutdown_storage() -> None:
    if uses_postgres():
        close_postgres_pool()
    reset_checkpointer()
    from app.runtime.graph_cache import clear_all_graph_caches

    clear_all_graph_caches()
