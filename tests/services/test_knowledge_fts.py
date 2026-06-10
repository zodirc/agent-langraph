"""P1-1 SQLite FTS5 keyword search path."""

import pytest

from app.config.settings import settings
from app.services.knowledge_store import get_knowledge_store


@pytest.fixture
def fts_enabled(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_FTS_ENABLED", True)


def test_keyword_search_fts_finds_document(fts_enabled, isolated_stores):
    store = get_knowledge_store()
    doc_id = store.upsert_document(
        "Policy",
        "REVIEW HIGH CRITICAL policy engine rules for deployment",
        {"domain": "common"},
    )
    hits = store.keyword_search("policy engine deployment", top_k=5)
    ids = {h["doc_id"] for h in hits}
    assert doc_id in ids


def test_fts_delete_removes_from_index(fts_enabled, isolated_stores):
    store = get_knowledge_store()
    doc_id = store.upsert_document("Temp", "unique FTS delete marker zebra", {})
    assert store.keyword_search("zebra marker", top_k=3)
    store._delete_row(doc_id)
    hits = store.keyword_search("zebra marker", top_k=3)
    assert all(h["doc_id"] != doc_id for h in hits)
