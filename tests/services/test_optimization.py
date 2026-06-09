"""Tests for optimization.md implementation."""

from __future__ import annotations

from app.runtime.state import merge_state
from app.services.artifact_read_cache import clear_all_read_cache
from app.services.artifact_tools import (
    _find_fuzzy_span,
    _normalize_edit_text,
    handle_edit_text_artifact,
    handle_read_text_artifact,
    handle_write_text_artifact,
)
from app.services.edit_scope import analyze_edit_scope, classify_edit_action, steer_implies_global_rewrite
from app.services.mission_stall_guard import compute_plan_signature, detect_stall
from app.services.mission_steer import apply_steer_planning_gate, steer_requires_planning
from app.services.steer_planning_lifecycle import settle_steer_planning_on_contract
from app.services.turn_contract_lifecycle import REASON_INCONSISTENT, invalidate_turn_contract_payload


def test_steer_implies_global_rewrite():
    assert steer_implies_global_rewrite("应该基于原电影人物，而不是一堆架空人物")
    assert classify_edit_action("换成原电影全部人物", outline_exists=True) == "rewrite_outline"


def test_settle_steer_planning_on_contract():
    payload = {
        "require_planning_after_steer": True,
        "turn_contract": {"primary_op": "edit_plot", "ops": []},
    }
    out = settle_steer_planning_on_contract(payload)
    assert out.get("steer_planning_done") is True
    assert out.get("steer_contract_pinned") is True
    assert steer_requires_planning(out) is False


def test_pinned_contract_not_invalidated_on_inconsistent():
    payload = {
        "steer_planning_done": True,
        "steer_contract_pinned": True,
        "turn_contract": {"primary_op": "edit_plot"},
        "selected_tools": ["read_text_artifact"],
    }
    out = invalidate_turn_contract_payload(payload, REASON_INCONSISTENT)
    assert out.get("turn_contract") is not None
    assert out.get("require_planning_after_contract_invalidation") is not True


def test_new_steer_clears_pin():
    payload = settle_steer_planning_on_contract(
        {"turn_contract": {"primary_op": "edit_plot"}, "require_planning_after_steer": True}
    )
    out = apply_steer_planning_gate(payload)
    assert out.get("steer_contract_pinned") is None
    assert out.get("steer_planning_done") is False


def test_read_cache_reuses_content(tmp_path, test_settings, monkeypatch):
    clear_all_read_cache()
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    handle_write_text_artifact(
        {"task_id": "cache-task", "filename": "outline.txt", "content": "alpha beta gamma"}
    )
    first = handle_read_text_artifact(
        {"task_id": "cache-task", "filename": "outline.txt", "max_chars": 8000}
    )
    second = handle_read_text_artifact(
        {"task_id": "cache-task", "filename": "outline.txt", "max_chars": 8000}
    )
    assert first["content"] == second["content"]
    assert first is not second


def test_fuzzy_edit_matches_escaped_noise():
    target = "林深（Neo）\\- 主角"
    old = "林深（Neo）- 主角"
    span = _find_fuzzy_span(target, old)
    assert span is not None
    matched = _normalize_edit_text(target[span[0] : span[1]])
    assert _normalize_edit_text(old).startswith(matched) or matched.startswith(
        _normalize_edit_text(old)[:8]
    )


def test_batch_edit(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "app.services.artifact_tools.get_audit_store",
        lambda: type("_Audit", (), {"append_events": lambda self, task_id, events: None})(),
    )
    handle_write_text_artifact(
        {
            "task_id": "batch",
            "filename": "outline.txt",
            "content": "林深\n苏棠\n白川",
        }
    )
    result = handle_edit_text_artifact(
        {
            "task_id": "batch",
            "filename": "outline.txt",
            "edits": [
                {"old_text": "林深", "new_text": "Neo", "replace_all": True},
                {"old_text": "苏棠", "new_text": "Morpheus", "replace_all": True},
                {"old_text": "白川", "new_text": "Trinity", "replace_all": True},
            ],
        }
    )
    text = (tmp_path / "artifacts" / "batch" / "outline.txt").read_text(encoding="utf-8")
    assert "Neo" in text and "Morpheus" in text and "Trinity" in text
    assert result["replacements"] == 3


def test_stall_detection(base_state):
    payload = {
        "turn_contract": {"primary_op": "edit_plot"},
        "mission_intervention": {"action": "edit_plot", "reason": "改人物"},
        "writing_command": {"target_filename": "outline.txt", "action": "edit_plot"},
    }
    state = merge_state(
        base_state,
        input_payload=payload,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1000},
        observation={"artifact_delta": {"has_change": False}},
    )
    from app.runtime.state_field_access import set_progress_on_state

    sig = compute_plan_signature(state)
    state = set_progress_on_state(state, {"plan_signature_history": [sig, sig]})
    stall = detect_stall(state)
    assert stall is not None
    assert stall.get("stalled") is True


def test_analyze_edit_scope_quantitative():
    scope = analyze_edit_scope(
        "林深→Neo、苏棠→Morpheus、白川→Trinity、陆衡→Smith",
        outline_excerpt="林深 苏棠 白川 陆衡 " * 20,
    )
    assert scope.anchor_count >= 4
    assert scope.estimated_span_ratio > 0


def test_stall_deterministic_payload():
    from app.services.mission_stall_execute import apply_stall_deterministic_payload

    out = apply_stall_deterministic_payload(
        {"goal": "x"}, signature="sig-a"
    )
    assert out.get("stall_force_deterministic_edit") is True
    assert out.get("skip_planning_llm") is True
    assert "sig-a" in (out.get("stall_deterministic_tried_signatures") or [])


def test_steer_progress_summary(base_state):
    from app.services.steer_progress_report import build_steer_progress_summary

    state = merge_state(
        base_state,
        mission_step=3,
        manuscript={"outline_bytes": 12000, "body_bytes": 0},
        input_payload={"steer_turn_started_at": "2026-01-01T00:00:00+00:00"},
    )
    summary = build_steer_progress_summary(state, elapsed_sec=95)
    assert "95" in summary
    assert "12000" in summary


def test_plan_signature_stable(base_state):
    state = merge_state(
        base_state,
        input_payload={
            "turn_contract": {"primary_op": "edit_plot"},
            "writing_command": {"target_filename": "outline.txt", "action": "edit_plot"},
        },
    )
    sig1 = compute_plan_signature(state)
    sig2 = compute_plan_signature(state)
    assert sig1 == sig2
    assert "edit_plot" in sig1
