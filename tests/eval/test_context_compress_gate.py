"""CI gate: context compression ratio threshold (mirrors RAG eval pattern)."""

from __future__ import annotations

from tests.eval.eval_thresholds import check_context_compress_thresholds


def test_check_context_compress_thresholds_pass():
    snapshot = {
        "compress_semantic": {"ratio": 0.65},
        "compress_character": {"ratio": 0.55},
    }
    assert not check_context_compress_thresholds(snapshot, min_ratio=0.5)


def test_check_context_compress_thresholds_fail():
    snapshot = {"compress_semantic": {"ratio": 0.3}}
    errors = check_context_compress_thresholds(snapshot, min_ratio=0.5)
    assert errors
    assert "0.300" in errors[0] or "0.3" in errors[0]
