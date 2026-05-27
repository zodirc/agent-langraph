"""PostgreSQL connection pool and business schema (production storage)."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator, Iterator

from app.config.settings import settings

logger = logging.getLogger(__name__)

_pool: Any | None = None
_TENANT_SEARCH_PATH_LAST: dict[int, str] = {}

EMBEDDING_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embedding_meta (
    id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    distance_metric TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

BUSINESS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_states (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_node TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_task_id ON audit_events (task_id);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL,
    errors TEXT NOT NULL,
    retry_count INTEGER NOT NULL,
    enqueued_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT NOT NULL,
    payload TEXT NOT NULL,
    embedding TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'episode',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL
);

""" + EMBEDDING_META_TABLE_SQL.strip() + ";\n"


def uses_postgres() -> bool:
    return settings.STORAGE_BACKEND == "postgres" and bool(settings.POSTGRES_URL)


def _sanitize_schema_name(schema: str) -> str:
    safe = "".join(ch for ch in schema if ch.isalnum() or ch in ("_", "-")).replace("-", "_")
    safe = safe[:63]
    return safe or "public"


def _current_tenant_schema() -> str:
    from app.services.tenant_context import get_tenant_id, postgres_schema_for_tenant

    tenant_id = get_tenant_id() if getattr(settings, "MULTI_TENANT_ENABLED", False) else None
    return _sanitize_schema_name(postgres_schema_for_tenant(tenant_id))


def _apply_tenant_search_path(conn: Any) -> None:
    """
    Apply PostgreSQL search_path for the current tenant.

    We keep a tiny per-connection cache to avoid repeating SET for every query.
    """
    if not uses_postgres() or not getattr(settings, "MULTI_TENANT_ENABLED", False):
        return
    schema = _current_tenant_schema()
    key = id(conn)
    if _TENANT_SEARCH_PATH_LAST.get(key) == schema:
        return
    conn.execute(f"SET search_path TO {schema}, public")
    _TENANT_SEARCH_PATH_LAST[key] = schema


def create_tenant_schema(tenant_id: str) -> str:
    """Create tenant schema + business tables; returns schema name."""
    if not uses_postgres():
        raise RuntimeError("create_tenant_schema requires STORAGE_BACKEND=postgres")
    from app.services.tenant_context import postgres_schema_for_tenant

    schema = _sanitize_schema_name(postgres_schema_for_tenant(tenant_id))
    pool = get_postgres_pool()
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"SET search_path TO {schema}, public")
        statements = [s.strip() for s in BUSINESS_SCHEMA_SQL.split(";") if s.strip()]
        for statement in statements:
            conn.execute(statement)
    return schema


def drop_tenant_schema(tenant_id: str) -> str:
    """Drop tenant schema; returns schema name."""
    if not uses_postgres():
        raise RuntimeError("drop_tenant_schema requires STORAGE_BACKEND=postgres")
    from app.services.tenant_context import postgres_schema_for_tenant

    schema = _sanitize_schema_name(postgres_schema_for_tenant(tenant_id))
    pool = get_postgres_pool()
    with pool.connection() as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    return schema


def get_postgres_pool() -> Any:
    global _pool
    if _pool is not None:
        return _pool
    if not settings.POSTGRES_URL:
        raise RuntimeError("STORAGE_BACKEND=postgres requires storage.postgres_url / DATABASE_URL")

    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _pool = ConnectionPool(
        settings.POSTGRES_URL,
        min_size=1,
        max_size=settings.POSTGRES_POOL_MAX_SIZE,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    logger.info("PostgreSQL pool ready (max_size=%s)", settings.POSTGRES_POOL_MAX_SIZE)
    return _pool


@contextmanager
def postgres_connection() -> Generator[Any, None, None]:
    pool = get_postgres_pool()
    with pool.connection() as conn:
        _apply_tenant_search_path(conn)
        yield conn


POSTGRES_SCHEMA_MIGRATIONS = (
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS session_id TEXT NOT NULL DEFAULT ''",
    EMBEDDING_META_TABLE_SQL.strip(),
)


def ensure_embedding_meta_table() -> None:
    """Ensure embedding_meta exists (Postgres tenants / legacy schemas)."""
    if not uses_postgres():
        return
    with postgres_connection() as conn:
        conn.execute(EMBEDDING_META_TABLE_SQL.strip())


def init_postgres_schema() -> None:
    statements = [s.strip() for s in BUSINESS_SCHEMA_SQL.split(";") if s.strip()]
    with postgres_connection() as conn:
        for statement in statements:
            conn.execute(statement)
        for migration in POSTGRES_SCHEMA_MIGRATIONS:
            conn.execute(migration)


def close_postgres_pool() -> None:
    global _pool, _read_pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")
    if _read_pool is not None:
        _read_pool.close()
        _read_pool = None
        logger.info("PostgreSQL read pool closed")


def postgres_pool_stats() -> dict[str, int]:
    """Expose connection pool stats for health/metrics."""
    if not uses_postgres():
        return {"active": 0, "idle": 0, "waiting": 0}
    pool = get_postgres_pool()
    try:
        stats = pool.get_stats()
        return {
            "active": int(getattr(stats, "connections_in_use", 0) or stats.get("connections_in_use", 0)),
            "idle": int(getattr(stats, "connections_available", 0) or stats.get("connections_available", 0)),
            "waiting": int(getattr(stats, "requests_waiting", 0) or stats.get("requests_waiting", 0)),
        }
    except Exception:
        return {"active": 0, "idle": 0, "waiting": 0}


def get_postgres_read_url() -> str:
    """Optional read replica URL for read-heavy endpoints."""
    import os

    return os.environ.get("DATABASE_READ_URL", "").strip() or settings.POSTGRES_URL


_read_pool: Any | None = None


def get_postgres_read_pool() -> Any:
    """Separate pool for read replica when DATABASE_READ_URL is set."""
    global _read_pool
    read_url = get_postgres_read_url()
    if not read_url or read_url == settings.POSTGRES_URL:
        return get_postgres_pool()
    if _read_pool is not None:
        return _read_pool
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _read_pool = ConnectionPool(
        read_url,
        min_size=1,
        max_size=settings.POSTGRES_POOL_MAX_SIZE,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    logger.info("PostgreSQL read pool ready")
    return _read_pool


@contextmanager
def postgres_read_connection() -> Generator[Any, None, None]:
    """Read-only connection (replica when configured)."""
    pool = get_postgres_read_pool()
    with pool.connection() as conn:
        _apply_tenant_search_path(conn)
        yield conn


def postgres_uses_read_replica() -> bool:
    import os

    url = os.environ.get("DATABASE_READ_URL", "").strip()
    return bool(url) and url != settings.POSTGRES_URL


def run_with_retry(
    operation: str,
    fn: Any,
    *,
    max_retries: int | None = None,
    retryable: tuple[str, ...] = ("locked", "disk I/O"),
) -> Any:
    """Retry transient DB errors (SQLite); no-op extra retries for PostgreSQL."""
    retries = max_retries if max_retries is not None else settings.DB_SAVE_MAX_RETRIES
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            message = str(exc).lower()
            if not any(token in message for token in retryable) or attempt == retries - 1:
                raise
            delay = settings.DB_SAVE_RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "%s failed (%s), retry %s/%s in %.2fs",
                operation,
                exc,
                attempt + 1,
                retries,
                delay,
            )
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{operation}: retry loop exited unexpectedly")
