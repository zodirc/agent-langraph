"""Rebuild vector index when embedding model changes."""

from __future__ import annotations

import logging
from typing import Any

from app.services.embedding_meta import get_current_embedding_meta

logger = logging.getLogger(__name__)


def reindex_collection(store: Any, *, limit: int = 10_000) -> dict[str, Any]:
    """
    Re-embed all documents in KnowledgeStore and refresh vector index.
    Returns summary dict with counts.
    """
    current = get_current_embedding_meta()
    docs = []
    for row in store.list_documents(limit=limit):
        full = store.get_document(row["doc_id"])
        if full:
            docs.append(full)

    rebuilt = 0
    for doc in docs:
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        meta = {**meta, "embedding_version": current.version}
        store.upsert_document(
            doc["title"],
            doc["content"],
            metadata=meta,
            doc_id=doc["doc_id"],
        )
        rebuilt += 1

    if hasattr(store, "save_embedding_meta"):
        store.save_embedding_meta(current)

    return {
        "rebuilt": rebuilt,
        "new_dimension": current.dimension,
        "model": current.model_name,
        "version": current.version,
    }
