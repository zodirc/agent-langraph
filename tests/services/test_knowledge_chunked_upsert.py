import json

from app.services.knowledge_store import get_knowledge_store


def _long_writing_body() -> str:
    sections = []
    for i in range(1, 9):
        sections.append(f"## 第{i}节 写作要点")
        sections.append("保持自然口吻，避免机械重复。" * 40)
    return "\n\n".join(sections)


def test_upsert_document_creates_searchable_chunks(isolated_stores, test_settings):
    store = get_knowledge_store()
    parent_id = store.upsert_document(
        "写作规范",
        _long_writing_body(),
        {"domain": "writing", "topic": "writing"},
        doc_id="chunked-parent-doc",
    )
    assert parent_id == "chunked-parent-doc"

    rows = store._iter_all_rows()
    searchable = [
        r
        for r in rows
        if (meta := json.loads(r["metadata"]))
        and not meta.get("is_parent")
    ]
    assert len(searchable) >= 3

    hits = store.hybrid_search("自然口吻", top_k=8, domains={"writing"})
    parent_ids = {
        (h.get("metadata") or {}).get("parent_doc_id") or h.get("doc_id") for h in hits
    }
    assert "chunked-parent-doc" in parent_ids


def test_get_document_reassembles_parent(isolated_stores, test_settings):
    store = get_knowledge_store()
    body = _long_writing_body()
    parent_id = store.upsert_document(
        "写作规范",
        body,
        {"domain": "writing"},
        doc_id="chunked-parent-doc-2",
    )
    doc = store.get_document(parent_id)
    assert doc is not None
    assert len(doc["content"]) >= len(body) * 0.5


def test_delete_document_removes_chunks(isolated_stores, test_settings):
    store = get_knowledge_store()
    parent_id = store.upsert_document(
        "临时",
        _long_writing_body(),
        {"domain": "writing"},
        doc_id="chunked-parent-doc-3",
    )
    assert store.delete_document(parent_id)
    remaining = [r for r in store._iter_all_rows() if str(r["doc_id"]).startswith(parent_id)]
    assert remaining == []
