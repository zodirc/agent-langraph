from app.services.manuscript_context import extract_chapter_text
from app.services.writing_phases import (
    apply_writing_phase_from_decision,
    mark_phase_done,
    normalize_writing_phase,
    suggest_writing_phase_fallback,
)
from app.runtime.state import create_initial_state, merge_state


def test_extract_chapter_text():
    body = "### 第1章 开端\n\n甲。\n\n### 第2章 发展\n\n乙。"
    assert "甲" in extract_chapter_text(body, 1)
    assert "乙" in extract_chapter_text(body, 2)
    assert extract_chapter_text(body, 3) == ""


def test_normalize_writing_phase_aliases():
    assert normalize_writing_phase("review") == "review_chapter"
    assert normalize_writing_phase("polish") == "polish_chapter"


def test_apply_writing_phase_from_decision(base_state):
    state = merge_state(
        base_state,
        step_decision={
            "action": "continue",
            "next_executor": "subgraph:writing",
            "params": {"writing_phase": "review_chapter", "chapter_index": 2},
        },
        mission={"kind": "writing", "step_policy": {"chars_per_step": 4000}},
    )
    updated = apply_writing_phase_from_decision(state)
    intent = (updated.get("input_payload") or {}).get("writing_intent") or {}
    assert intent.get("action") == "review_chapter"
    assert intent.get("chapter_index") == 2


def test_suggest_fallback_outline_first(base_state):
    state = merge_state(
        base_state,
        mission={
            "kind": "writing",
            "success_criteria": {"type": "metric_gte", "metric": "written_chars", "target": 10000},
            "orchestration": {"enabled": True},
        },
        progress={"metrics": {"written_chars": 0}},
        manuscript={"outline_bytes": 0, "body_path": "novel.txt"},
    )
    d = suggest_writing_phase_fallback(state)
    assert d.params.get("writing_phase") == "write_outline"


def test_suggest_fallback_skips_repeat_chapter_summary(base_state, monkeypatch):
    body = "### 第1章 开端\n\n正文足够长。" * 20
    monkeypatch.setattr(
        "app.services.writing_phases.read_body_text",
        lambda *_a, **_k: body,
    )
    state = merge_state(
        base_state,
        mission={
            "kind": "writing",
            "success_criteria": {"type": "metric_gte", "metric": "written_chars", "target": 100000},
            "orchestration": {"enabled": True},
        },
        progress={
            "metrics": {"written_chars": 5000},
            "writing_state": {"phases_done": {"1": ["chapter_summary"]}},
        },
        manuscript={"outline_bytes": 500, "body_path": "novel.txt", "body_bytes": 5000},
    )
    d = suggest_writing_phase_fallback(state)
    assert d.params.get("writing_phase") != "chapter_summary"


def test_mark_phase_done_persisted_in_progress(base_state):
    state = mark_phase_done(base_state, 1, "chapter_summary")
    ws = (state.get("progress") or {}).get("writing_state") or {}
    assert "chapter_summary" in (ws.get("phases_done") or {}).get("1", [])
