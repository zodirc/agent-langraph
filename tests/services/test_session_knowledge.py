"""Session-scoped source knowledge: isolation, domains, grounding admission."""

from __future__ import annotations

from app.runtime.state import create_initial_state
from app.services.knowledge_store import get_knowledge_store
from app.services.retrieval_policy import retrieval_domains_for_state
from app.services.session_scope import set_retrieval_session_id
from app.services.state_store import get_state_store
from app.services.task_cleanup import purge_task_remains
from app.services.writing_context import (
    build_session_source_excerpt,
    writing_style_only_evidence,
)


def test_session_source_isolated_between_sessions(isolated_stores):
    store = get_knowledge_store()
    store.upsert_document(
        "岁月剧情",
        "梁致远是主人公，讲述官场沉浮。",
        metadata={"domain": "source"},
        session_id="sess-a",
    )
    store.upsert_document(
        "其他素材",
        "完全不同的故事线。",
        metadata={"domain": "source"},
        session_id="sess-b",
    )
    store.upsert_document(
        "全局规范",
        "长文写作总则。",
        metadata={"domain": "writing"},
    )

    set_retrieval_session_id("sess-a")
    try:
        source_hits = store.hybrid_search("梁致远", domains={"source"})
        writing_hits = store.hybrid_search("长文写作总则", domains={"writing"})
    finally:
        set_retrieval_session_id(None)

    source_text = " ".join(h.get("content") or "" for h in source_hits)
    assert "梁致远" in source_text
    assert "完全不同的故事" not in source_text
    assert any("总则" in (h.get("content") or "") for h in writing_hits)


def test_session_docs_hidden_without_scope(isolated_stores):
    store = get_knowledge_store()
    store.upsert_document(
        "私密设定",
        "仅本会话可见的设定。",
        metadata={"domain": "source"},
        session_id="sess-private",
    )

    hits = store.hybrid_search("私密设定", domains={"source"})
    assert not hits


def test_retrieval_domains_for_writing_includes_source(isolated_stores):
    state = {"mission": {"kind": "writing"}, "input_payload": {}}
    assert retrieval_domains_for_state(state) == {"writing", "common", "source"}


def test_writing_style_only_false_when_source_present():
    style_only = {
        "retrieved_knowledge": [
            {"doc_id": "w1", "metadata": {"domain": "writing"}},
            {"doc_id": "c1", "metadata": {"domain": "common"}},
        ]
    }
    with_source = {
        "retrieved_knowledge": [
            {"doc_id": "w1", "metadata": {"domain": "writing"}},
            {"doc_id": "s1", "metadata": {"domain": "source"}},
        ]
    }
    assert writing_style_only_evidence(style_only) is True
    assert writing_style_only_evidence(with_source) is False


def test_build_session_source_excerpt():
    state = {
        "retrieved_knowledge": [
            {
                "doc_id": "src-1",
                "content": "《岁月》主要人物：梁致远。",
                "metadata": {"domain": "source"},
            },
            {
                "doc_id": "w1",
                "content": "排版规范。",
                "metadata": {"domain": "writing"},
            },
        ]
    }
    excerpt = build_session_source_excerpt(state)
    assert "梁致远" in excerpt
    assert "排版规范" not in excerpt


def test_delete_by_session_removes_only_session_docs(isolated_stores):
    store = get_knowledge_store()
    store.upsert_document(
        "会话素材",
        "会话私有剧情。",
        metadata={"domain": "source"},
        session_id="sess-del",
    )
    global_id = store.upsert_document(
        "全局规范",
        "全局写作规范。",
        metadata={"domain": "writing"},
    )

    removed = store.delete_by_session("sess-del")
    assert removed == 1

    set_retrieval_session_id("sess-del")
    try:
        assert not store.hybrid_search("会话私有", domains={"source"})
    finally:
        set_retrieval_session_id(None)

    assert store.get_document(global_id) is not None


def test_purge_task_removes_session_knowledge(isolated_stores, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.task_cleanup.settings.ARTIFACTS_PATH",
        str(tmp_path / "artifacts"),
    )
    task_id = "sess-purge-kb"
    state = create_initial_state(user_id="u1", input_payload={"goal": "写小说"})
    state["task_id"] = task_id
    state["session_id"] = task_id
    get_state_store().save(state)

    store = get_knowledge_store()
    store.upsert_document(
        "素材",
        "任务绑定素材。",
        metadata={"domain": "source"},
        session_id=task_id,
    )

    result = purge_task_remains(task_id)
    assert result["session_knowledge_removed"] == 1
    assert store.delete_by_session(task_id) == 0
