"""P1-1 query rewrite helpers."""

import pytest

from app.runtime.evidence_models import QueryObject
from app.services.query_builder import expand_multi_queries


def test_expand_multi_queries_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.query_builder.settings.RETRIEVAL_MULTI_QUERY_ENABLED",
        False,
    )
    qo = QueryObject(
        original_query="fix it",
        standalone_query="fix deployment error",
        must_have_terms=["DEPLOY_ERR"],
    )
    assert expand_multi_queries(qo) == ["fix deployment error"]


def test_expand_multi_queries_adds_must_have_variant(monkeypatch):
    monkeypatch.setattr(
        "app.services.query_builder.settings.RETRIEVAL_MULTI_QUERY_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.query_builder.settings.RETRIEVAL_MULTI_QUERY_MAX",
        3,
    )
    qo = QueryObject(
        original_query="fix it",
        standalone_query="fix deployment error",
        must_have_terms=["DEPLOY_ERR"],
        soft_terms=[],
    )
    queries = expand_multi_queries(qo)
    assert len(queries) >= 2
    assert any("DEPLOY_ERR" in q for q in queries)
