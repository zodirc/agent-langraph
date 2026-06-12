"""§7 latency optimizations: cache, fast tier, embedding reuse, session-source fast answer."""

from unittest.mock import patch

import pytest

from app.config.settings import settings
from app.nodes.reasoning_node import reasoning_node
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.embedding_service import embed_text
from app.services.knowledge_store import get_knowledge_store
from app.services.query_embedding_context import (
    enter_query_embedding_scope,
    exit_query_embedding_scope,
    get_scoped_embedding,
)
from app.services.retrieval_cache import cache_get, cache_put, clear_cache, invalidate_session
from app.services.session_scope import set_retrieval_session_id
from app.services.session_source_fast_answer import try_session_source_fast_answer


@pytest.fixture(autouse=True)
def _clear_retrieval_cache():
    clear_cache()
    yield
    clear_cache()


def test_retrieval_cache_hit(isolated_stores):
    session_id = "sess-cache-1"
    domains = {"source", "common"}
    hits = [{"doc_id": "d1", "content": "岁月剧情", "metadata": {"domain": "source"}}]
    cache_put("岁月 素材", domains, session_id, hits)
    cached = cache_get("岁月 素材", domains, session_id)
    assert cached is not None
    assert cached[0]["doc_id"] == "d1"
    invalidate_session(session_id)
    assert cache_get("岁月 素材", domains, session_id) is None


def test_query_embedding_scope_reuse(monkeypatch):
    calls: list[str] = []

    def _fake_embed_texts(texts):
        calls.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr("app.services.embedding_service.embed_texts", _fake_embed_texts)
    enter_query_embedding_scope("same query")
    try:
        first = embed_text("same query")
        second = embed_text("same query")
        assert first == second
        assert calls == ["same query"]
        assert get_scoped_embedding("same query") == first
    finally:
        exit_query_embedding_scope()


def test_fast_keyword_search_skips_vector(isolated_stores, monkeypatch):
    store = get_knowledge_store()
    store.upsert_document(
        title="岁月人物",
        content="梁致远是主角，官场题材电视剧岁月的主要人物。",
        metadata={"domain": "source"},
        session_id="sess-fast",
    )
    set_retrieval_session_id("sess-fast")
    vector_called = {"n": 0}

    def _boom(*_a, **_k):
        vector_called["n"] += 1
        raise AssertionError("vector_search should not run in fast tier")

    monkeypatch.setattr(store, "vector_search", _boom)
    hits = store.fast_keyword_search("梁致远", domains={"source"})
    assert vector_called["n"] == 0
    assert hits
    set_retrieval_session_id(None)


def test_embedding_compat_check_once(isolated_stores, monkeypatch):
    store = get_knowledge_store()
    calls = {"n": 0}
    original_load = store.load_embedding_meta

    def _counting_load():
        calls["n"] += 1
        return original_load()

    monkeypatch.setattr(store, "load_embedding_meta", _counting_load)
    store._embedding_compat_checked = False
    monkeypatch.setattr(settings, "RETRIEVAL_EMBEDDING_COMPAT_ONCE", True)
    store.hybrid_search("test query", domains={"common"})
    store.hybrid_search("test query 2", domains={"common"})
    assert calls["n"] == 1


def test_session_source_fast_answer_with_hits():
    state = merge_state(
        create_initial_state(
            input_payload={
                "goal": "你看过我们的素材了么",
                "thin_execution_profile": "session_source_qa",
            }
        ),
        retrieved_knowledge=[
            {
                "doc_id": "src-1",
                "title": "岁月设定",
                "content": "梁致远人物小传",
                "metadata": {"domain": "source"},
            }
        ],
    )
    result = try_session_source_fast_answer(state)
    assert result is not None
    assert result.get("fast_reasoning") is True
    assert "岁月设定" in result["summary"] or "素材" in result["summary"]


def test_session_source_fast_answer_without_hits():
    state = create_initial_state(
        input_payload={
            "goal": "你看过我们的素材了么",
            "thin_execution_profile": "session_source_qa",
        }
    )
    result = try_session_source_fast_answer(state)
    assert result is not None
    assert "尚未检索到" in result["summary"] or "导入" in result["summary"]


def test_emit_static_reasoning_answer_pushes_answer_delta(monkeypatch):
    deltas: list[str] = []
    monkeypatch.setattr(
        "app.services.reasoning_trace.answer_stream_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.reasoning_trace.report_answer_delta",
        lambda **kwargs: deltas.append(str(kwargs.get("text") or "")),
    )
    from app.services.reasoning_trace import emit_static_reasoning_answer

    emit_static_reasoning_answer(
        {"summary": "是的，我已读取本会话素材。", "structured": {"source": "session_source_fast"}}
    )
    assert deltas == ["是的，我已读取本会话素材。"]


@patch("app.services.llm_client.invoke_structured")
@patch("app.services.llm_client.stream_structured")
def test_reasoning_session_source_qa_skips_llm(mock_stream, mock_invoke, isolated_stores):
    mock_stream.side_effect = AssertionError("reasoning LLM must not run for session_source_qa")
    mock_invoke.side_effect = AssertionError("reasoning LLM must not run for session_source_qa")
    state = merge_state(
        create_initial_state(
            input_payload={
                "goal": "你看过我们的素材了么",
                "thin_execution_profile": "session_source_qa",
            }
        ),
        retrieved_knowledge=[
            {
                "doc_id": "src-1",
                "title": "岁月",
                "content": "剧情主线",
                "metadata": {"domain": "source"},
            }
        ],
    )
    with patch("app.services.reasoning_trace.report_answer_delta") as mock_delta:
        out = reasoning_node(state)
    mock_stream.assert_not_called()
    mock_invoke.assert_not_called()
    mock_delta.assert_called()
    assert out.get("status") == TaskStatus.REASONED.value
    summary = (out.get("reasoning_result") or {}).get("summary") or ""
    assert "素材" in summary
