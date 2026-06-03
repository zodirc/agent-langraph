"""Tenant-scoped storage paths and provisioning (SQLite file

PG schema)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config.settings import settings
from app.services.db import create_tenant_schema, drop_tenant_schema, uses_postgres
from app.services.tenant_context import get_tenant_id

_TENANT_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tenant_id(tenant_id: str) -> str:
    safe = _TENANT_ID_SAFE.sub("", tenant_id)[:64]
    if not safe:
        raise ValueError("tenant_id must contain alphanumeric characters")
    return safe


def tenant_cache_key(tenant_id: str | None = None) -> str:
    """Cache key for per-tenant store singletons."""
    if not getattr(settings, "MULTI_TENANT_ENABLED", False) or uses_postgres():
        return "default"
    tid = tenant_id if tenant_id is not None else get_tenant_id()
    return sanitize_tenant_id(tid) if tid else "default"


def sqlite_path_for_tenant(tenant_id: str) -> str:
    """Return SQLite DB path for a tenant (`data/db/tenant_{id}.db`)."""
    safe = sanitize_tenant_id(tenant_id)
    base = Path(settings.SQLITE_PATH).parent
    return str(base / f"tenant_{safe}.db")


def vectorstore_path_for_tenant(tenant_id: str) -> str:
    """Return Chroma persist directory for a tenant."""
    safe = sanitize_tenant_id(tenant_id)
    return str(Path(settings.VECTORSTORE_PATH) / f"tenant_{safe}")


def current_sqlite_path() -> str:
    if not getattr(settings, "MULTI_TENANT_ENABLED", False) or uses_postgres():
        return settings.SQLITE_PATH
    tid = get_tenant_id()
    if not tid:
        return settings.SQLITE_PATH
    return sqlite_path_for_tenant(tid)


def current_vectorstore_path() -> str:
    if not getattr(settings, "MULTI_TENANT_ENABLED", False) or uses_postgres():
        return settings.VECTORSTORE_PATH
    tid = get_tenant_id()
    if not tid:
        return settings.VECTORSTORE_PATH
    return vectorstore_path_for_tenant(tid)


def _init_sqlite_tenant_stores(db_path: str, vector_path: str | None = None) -> None:
    from app.services.audit_store import AuditStore
    from app.services.dead_letter_store import DeadLetterStore
    from app.services.knowledge_store import KnowledgeStore
    from app.services.memory_store import MemoryStore
    from app.services.state_store import StateStore

    StateStore(db_path=db_path)
    AuditStore(db_path=db_path)
    DeadLetterStore(db_path=db_path)
    MemoryStore(db_path=db_path)
    KnowledgeStore(db_path=db_path, vector_path=vector_path or settings.VECTORSTORE_PATH)


def create_tenant_storage(tenant_id: str) -> dict[str, str]:
    """Provision isolated storage for a tenant (PG schema or SQLite file)."""
    safe = sanitize_tenant_id(tenant_id)
    if uses_postgres():
        schema = create_tenant_schema(safe)
        return {"backend": "postgres", "tenant_id": safe, "schema": schema}
    db_path = sqlite_path_for_tenant(safe)
    vector_path = vectorstore_path_for_tenant(safe)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(vector_path).mkdir(parents=True, exist_ok=True)
    _init_sqlite_tenant_stores(db_path, vector_path)
    return {"backend": "sqlite", "tenant_id": safe, "db_path": db_path, "vector_path": vector_path}


def drop_tenant_storage(tenant_id: str) -> dict[str, str]:
    """Remove tenant storage (PG schema or SQLite files)."""
    safe = sanitize_tenant_id(tenant_id)
    if uses_postgres():
        schema = drop_tenant_schema(safe)
        return {"backend": "postgres", "tenant_id": safe, "schema": schema}
    db_path = Path(sqlite_path_for_tenant(safe))
    vector_path = Path(vectorstore_path_for_tenant(safe))
    if db_path.exists():
        db_path.unlink()
    if vector_path.exists():
        shutil.rmtree(vector_path, ignore_errors=True)
    reset_tenant_store_caches()
    return {
        "backend": "sqlite",
        "tenant_id": safe,
        "db_path": str(db_path),
        "vector_path": str(vector_path),
    }


def reset_tenant_store_caches() -> None:
    """Clear per-tenant store singleton caches (tests)."""
    import app.services.audit_store as audit_mod
    import app.services.dead_letter_store as dlq_mod
    import app.services.knowledge_store as knowledge_mod
    import app.services.memory_store as memory_mod
    import app.services.state_store as state_mod

    state_mod._stores.clear()  # type: ignore[attr-defined]
    audit_mod._stores.clear()  # type: ignore[attr-defined]
    dlq_mod._stores.clear()  # type: ignore[attr-defined]
    memory_mod._stores.clear()  # type: ignore[attr-defined]
    knowledge_mod._stores.clear()  # type: ignore[attr-defined]
    state_mod._store = None
    audit_mod._store = None
    dlq_mod._store = None
    memory_mod._store = None
    knowledge_mod._store = None
