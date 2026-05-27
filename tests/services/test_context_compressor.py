from app.services.context_compressor import (
    SemanticContextSummary,
    apply_semantic_context_compress,
    build_semantic_context_summary,
)


def test_short_history_no_semantic_summary(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC",
        4000,
    )
    history = [{"role": "user", "content": "hello"}]
    assert build_semantic_context_summary(history, state={}) is None


def test_long_history_rule_based_summary(monkeypatch):
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
    history = [{"role": "user", "content": "x" * 200}]
    state = {
        "mission": {"objective": "写一篇 3000 字文章"},
        "turn_facts": {"tools_executed": [{"tool": "echo", "status": "ok"}]},
        "plan": ["step1", "step2"],
    }
    summary = build_semantic_context_summary(history, state=state)
    assert summary is not None
    assert "3000" in summary.goal or "文章" in summary.goal
    assert summary.executed_facts


def test_apply_semantic_compress_inserts_system_prefix(monkeypatch):
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC",
        50,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.MODEL_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.services.context_compressor.settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS",
        2,
    )
    history = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100},
        {"role": "user", "content": "recent"},
    ]
    result = apply_semantic_context_compress(history, state={"input_payload": {"goal": "test"}})
    assert result[0]["role"] == "system"
    assert "Semantic context" in result[0]["content"] or "Goal:" in result[0]["content"]


def test_summary_to_system_message():
    summary = SemanticContextSummary(goal="demo", executed_facts=["echo: ok"])
    msg = summary.to_system_message()
    assert msg["role"] == "system"
    assert "demo" in msg["content"]
