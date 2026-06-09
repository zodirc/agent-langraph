"""End-to-end checks for optimization.md §5 acceptance criteria."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.runtime.state import merge_state
from app.services.artifact_read_cache import clear_all_read_cache
from app.services.artifact_tools import handle_read_text_artifact, handle_write_text_artifact
from app.services.edit_scope import analyze_edit_scope, classify_edit_action
from app.services.mission_stall_execute import apply_stall_deterministic_payload, run_stall_deterministic_edit
from app.services.mission_steer import apply_steer_planning_gate, steer_requires_planning
from app.services.pre_planning import should_skip_edit_plot_planning_llm
from app.services.progress_evaluator import EvalResult, evaluate_mission_control
from app.services.steer_planning_lifecycle import settle_steer_planning_on_contract
from app.runtime.state_field_access import set_progress_on_state


STEER_MATRIX = "应该基于原电影人物，而不是一堆架空人物"


@pytest.fixture
def outline_state(base_state, tmp_path, test_settings, monkeypatch):
    clear_all_read_cache()
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    task_id = base_state["task_id"]
    outline = (
        "# 大纲\n\n"
        "## 人物\n"
        "- 林深（主角）\n"
        "- 苏棠（女主）\n"
        "- 白川（反派）\n"
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "outline.txt", "content": outline}
    )
    payload = apply_steer_planning_gate(
        {
            "goal": STEER_MATRIX,
            "latest_steer_message": STEER_MATRIX,
            "require_planning_after_steer": True,
            "mission": {"kind": "writing"},
        }
    )
    return merge_state(
        base_state,
        mission={"kind": "writing", "step_policy": {"then": "append_body"}},
        manuscript={"outline_path": "outline.txt", "outline_bytes": len(outline.encode("utf-8"))},
        input_payload=payload,
    )


def test_matrix_steer_classifies_as_rewrite():
    scope = analyze_edit_scope(STEER_MATRIX, outline_excerpt="林深\n苏棠\n白川")
    assert scope.global_keywords is True
    assert classify_edit_action(STEER_MATRIX, outline_exists=True, outline_excerpt="林深") == "rewrite_outline"


def test_planning_skip_for_local_edit_only(base_state):
    payload = apply_steer_planning_gate(
        {
            "goal": "把第三章标题改成决战",
            "latest_steer_message": "把第三章标题改成决战",
            "mission": {"kind": "writing"},
        }
    )
    state = merge_state(
        base_state,
        mission={"kind": "writing"},
        manuscript={"outline_path": "outline.txt", "outline_bytes": 500},
        input_payload=payload,
    )
    assert should_skip_edit_plot_planning_llm(state) is True


def test_read_cache_single_disk_hit(outline_state, monkeypatch):
    task_id = outline_state["task_id"]
    monkeypatch.setattr(
        "app.services.artifact_tools.read_artifact_tail",
        lambda *a, **k: "",
    )
    reads = {"n": 0}
    import app.services.artifact_tools as at

    orig = at.Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if self.name == "outline.txt":
            reads["n"] += 1
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(at.Path, "read_text", counting_read_text)
    handle_read_text_artifact({"task_id": task_id, "filename": "outline.txt", "max_chars": 12000})
    handle_read_text_artifact({"task_id": task_id, "filename": "outline.txt", "max_chars": 12000})
    assert reads["n"] == 1


def test_steer_settle_blocks_replan(outline_state):
    payload = settle_steer_planning_on_contract(
        {
            **(outline_state.get("input_payload") or {}),
            "turn_contract": {"primary_op": "rewrite_outline", "ops": []},
        }
    )
    assert payload.get("steer_planning_done") is True
    assert steer_requires_planning(payload) is False


def _stall_ready_state(outline_state):
    payload = settle_steer_planning_on_contract(
        {
            **(outline_state.get("input_payload") or {}),
            "turn_contract": {"primary_op": "edit_plot", "ops": []},
        }
    )
    return merge_state(outline_state, input_payload=payload)


def test_stall_first_hit_requests_deterministic_edit(outline_state, monkeypatch):
    monkeypatch.setattr("app.services.session_fsm.routing_needs_replan", lambda _s: False)
    from app.services.mission_stall_guard import compute_plan_signature

    ready = _stall_ready_state(outline_state)
    sig = compute_plan_signature(ready)
    state = set_progress_on_state(
        ready,
        {"plan_signature_history": [sig, sig], "stall_count": 0},
    )
    state = merge_state(state, observation={"artifact_delta": {"has_change": False}})
    result = evaluate_mission_control(state)
    assert isinstance(result, EvalResult)
    assert result.action == "continue"
    assert result.meta.get("stall_deterministic_edit") is True


def test_stall_budget_exceeded_pauses(outline_state, monkeypatch):
    monkeypatch.setattr("app.services.session_fsm.routing_needs_replan", lambda _s: False)
    from app.services.mission_stall_guard import compute_plan_signature

    ready = _stall_ready_state(outline_state)
    sig = compute_plan_signature(ready)
    state = set_progress_on_state(
        ready,
        {"plan_signature_history": [sig, sig], "stall_count": 2},
    )
    state = merge_state(state, observation={"artifact_delta": {"has_change": False}})
    result = evaluate_mission_control(state)
    assert result.action == "pause"
    assert result.meta.get("stall_clarification")


def test_stall_deterministic_edit_batch(monkeypatch, outline_state):
    plan = {
        "found": True,
        "edits": [
            {"old_text": "林深", "new_text": "Neo", "replace_all": True},
            {"old_text": "苏棠", "new_text": "Trinity", "replace_all": True},
            {"old_text": "白川", "new_text": "Morpheus", "replace_all": True},
        ],
        "batch": True,
    }
    monkeypatch.setattr(
        "app.services.outline_steer_patch.plan_edit_from_read_content",
        lambda *a, **k: plan,
    )
    ready = _stall_ready_state(outline_state)
    payload = apply_stall_deterministic_payload(
        {
            **dict(ready.get("input_payload") or {}),
            "latest_steer_message": "林深→Neo、苏棠→Trinity、白川→Morpheus",
            "goal": "林深→Neo、苏棠→Trinity、白川→Morpheus",
        },
        signature="edit_plot|outline.txt|x",
    )
    state = merge_state(ready, input_payload=payload)
    out = run_stall_deterministic_edit(state)
    tool_results = out.get("tool_results") or []
    edit = next((t for t in tool_results if t.get("tool") == "edit_text_artifact"), None)
    assert edit is not None
    assert edit.get("status") == "ok"
    from app.services.artifact_tools import task_artifact_dir

    text = (task_artifact_dir(state["task_id"]) / "outline.txt").read_text(encoding="utf-8")
    assert "Neo" in text and "Trinity" in text and "Morpheus" in text
    assert "林深" not in text


def test_diff_preview_streams_via_confirmation(monkeypatch, outline_state, test_settings, tmp_path):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    calls = []

    def fake_report(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.services.stream_progress.report_writing_delta", fake_report)
    store = MagicMock()
    store.load.return_value = outline_state
    monkeypatch.setattr("app.services.state_store.get_state_store", lambda: store)

    from app.services.confirmation.writing_delta import stream_edit_diff_preview

    stream_edit_diff_preview(
        task_id=outline_state["task_id"],
        filename="outline.txt",
        diff_preview="--- before\n+++ after\n+Neo",
    )
    assert calls
    assert calls[0].get("phase") == "diff_preview"
