from __future__ import annotations

from unittest.mock import patch

from app.services.db import BUSINESS_SCHEMA_SQL, EMBEDDING_META_TABLE_SQL
from app.services.knowledge_store import KnowledgeStore, get_knowledge_store


def test_business_schema_defines_embedding_meta():
    assert "embedding_meta" in BUSINESS_SCHEMA_SQL
    assert "model_name" in EMBEDDING_META_TABLE_SQL


def test_hybrid_search_survives_embedding_meta_load_failure(isolated_stores):
    store = get_knowledge_store()
    doc_id = store.upsert_document("One Piece", "Luffy pirate king adventure manga", {})
    original_fetchone = store._fetchone

    def fetchone_with_broken_meta(query: str, params: tuple = ()):
        if "embedding_meta" in query:
            raise RuntimeError('relation "embedding_meta" does not exist')
        return original_fetchone(query, params)

    with patch.object(store, "_fetchone", side_effect=fetchone_with_broken_meta):
        hits = store.hybrid_search("One Piece pirate")
    assert isinstance(hits, list)
    assert any(h["doc_id"] == doc_id for h in hits)


def test_load_embedding_meta_returns_none_on_db_error(tmp_path):
    store = KnowledgeStore(db_path=str(tmp_path / "agent.db"), vector_path=str(tmp_path / "vs"))
    with patch.object(store, "_fetchone", side_effect=RuntimeError("no such table: embedding_meta")):
        assert store.load_embedding_meta() is None


def test_check_embedding_compatibility_does_not_raise_on_save_failure(isolated_stores):
    store = get_knowledge_store()
    with patch.object(store, "save_embedding_meta", side_effect=RuntimeError("disk full")):
        store._check_embedding_compatibility()
