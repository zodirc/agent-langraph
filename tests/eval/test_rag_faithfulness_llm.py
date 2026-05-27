"""LLM faithfulness path (mocked) for CI without live model."""

from __future__ import annotations

import json

import pytest

from app.services.rag_eval import check_faithfulness
from tests.eval.eval_metrics import record


def test_llm_faithfulness_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.rag_eval.settings.RAG_FAITHFULNESS_LLM_ENABLED", True)
    monkeypatch.setattr("app.services.rag_eval.settings.MODEL_ENABLED", True)
    monkeypatch.setattr("app.services.rag_eval.settings.RAG_FAITHFULNESS_THRESHOLD", 0.7)

    def _fake_invoke(purpose: str, system: str, user: str, **kwargs: object) -> dict:
        payload = json.loads(user)
        answer = payload.get("answer", "")
        if "Berlin" in answer:
            return {"faithful": False, "score": 0.2, "unsupported_claims": ["Berlin"]}
        return {"faithful": True, "score": 0.92, "unsupported_claims": []}

    monkeypatch.setattr("app.services.llm_client.invoke_structured", _fake_invoke)

    docs = [{"doc_id": "d1", "content": "The capital of France is Paris."}]
    good = check_faithfulness("The capital of France is Paris.", docs)
    bad = check_faithfulness("The capital of France is Berlin.", docs)

    assert good["faithful"] is True
    assert good["score"] >= 0.7
    assert bad["faithful"] is False
    record("rag_llm_faithfulness_good", score=float(good["score"]))
    record("rag_llm_faithfulness_bad", score=float(bad["score"]))


def test_llm_faithfulness_fallback_on_invoke_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.rag_eval.settings.RAG_FAITHFULNESS_LLM_ENABLED", True)
    monkeypatch.setattr("app.services.rag_eval.settings.MODEL_ENABLED", True)

    def _boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm_client.invoke_structured", _boom)

    docs = [{"doc_id": "d1", "content": "Python was created by Guido van Rossum"}]
    result = check_faithfulness("Python was created by Guido", docs)
    assert "faithful" in result
    assert "score" in result
