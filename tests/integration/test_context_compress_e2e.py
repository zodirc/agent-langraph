"""E2E: semantic context compression + Prometheus ratio gate (v0.12)."""

from __future__ import annotations

from app.config.settings import settings
from app.services.context_compressor import (
    _history_char_count,
    apply_semantic_context_compress,
    compression_ratio,
    record_context_compress_metrics,
)
from app.services.metrics_service import get_metrics_service
from app.services.session_turn import compress_session_history


def _long_history(turns: int = 12, chars_per_turn: int = 400) -> list[dict]:
    history: list[dict] = []
    for i in range(turns):
        history.append({"role": "user", "content": f"question-{i} " + "x" * chars_per_turn})
        history.append(
            {"role": "assistant", "content": f"answer-{i} " + "y" * chars_per_turn}
        )
    return history


def test_semantic_compress_ratio_meets_gate(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC",
        100,
    )
    monkeypatch.setattr(
        "app.services.session_turn.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.session_turn.settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC",
        100,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.MODEL_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS",
        2,
    )

    history = _long_history()
    before = _history_char_count(history)
    compressed = apply_semantic_context_compress(
        history,
        state={"input_payload": {"goal": "write a long article"}, "plan": ["draft", "review"]},
    )
    after = _history_char_count(compressed)
    ratio = compression_ratio(before, after)
    min_ratio = settings.CONTEXT_COMPRESS_MIN_RATIO
    assert ratio >= min_ratio, f"compression ratio {ratio:.2f} < min {min_ratio}"
    assert compressed[0]["role"] == "system"
    assert len(compressed) < len(history)


def test_compress_session_history_records_metrics(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_turn.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC",
        100,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.MODEL_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS",
        2,
    )

    history = _long_history(turns=10, chars_per_turn=250)
    state_ctx = {"input_payload": {"goal": "continue writing"}, "plan": ["outline", "draft"]}
    get_metrics_service()._context_compress_ratios.clear()  # type: ignore[attr-defined]
    compressed = compress_session_history(history, state=state_ctx)
    assert compressed
    samples = get_metrics_service().context_compress_ratio_samples()
    assert samples, "expected compress metrics after compress_session_history"
    method, recorded = samples[-1]
    assert method == "semantic"
    assert recorded >= settings.CONTEXT_COMPRESS_MIN_RATIO


def test_record_context_compress_metrics_in_memory():
    svc = get_metrics_service()
    svc._context_compress_ratios.clear()  # type: ignore[attr-defined]
    before = [{"role": "user", "content": "a" * 500}]
    after = [{"role": "system", "content": "summary"}, {"role": "user", "content": "hi"}]
    ratio = record_context_compress_metrics(before, after, method="semantic")
    assert ratio > 0.5
    assert svc.mean_context_compress_ratio(method="semantic") == ratio


def test_compress_session_history_character_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_turn.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.services.session_turn.settings.SESSION_COMPRESS_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.session_turn.settings.SESSION_MAX_HISTORY_TURNS",
        4,
    )
    monkeypatch.setattr(
        "app.services.session_turn.settings.SESSION_MAX_HISTORY_CHARS",
        200,
    )
    history = _long_history(turns=6, chars_per_turn=80)
    svc = get_metrics_service()
    svc._context_compress_ratios.clear()  # type: ignore[attr-defined]
    out = compress_session_history(history)
    assert _history_char_count(out) <= 200 + 120
    samples = svc.context_compress_ratio_samples()
    assert any(m == "character" for m, _ in samples)
