"""Tests for revision fast path (revision_intent_control_plan)."""

from __future__ import annotations

import pytest

from app.domain.revision_intent import RevisionEdit, RevisionIntent
from app.services.artifact_tools import handle_read_text_artifact, task_artifact_dir
from app.services.event_classification import classify_user_event
from app.services.intent_snapshot import freeze_intent_snapshot, get_or_freeze_intent_snapshot, hash_user_input
from app.services.planning_gate_policy import derive_planning_required
from app.services.revision_detection import detect_structural_revision, infer_revision_intent_structural
from app.services.confirmation.revision_boundary import (
    build_revision_boundary_text,
    needs_revision_clarification,
    scope_resolution_confidence,
)
from app.services.intent_composer import classify_revision_override
from app.services.revision_context import is_revision_continuation_goal, merge_continuation_revision_intent
from app.services.revision_done import is_revision_done, is_revision_turn, mark_revision_completed
from app.services.turn_contract import revision_edit_plot_contract
from app.services.task_drift import detect_task_drift
from app.services.writing.revision_command import revision_intent_executable, revision_intent_to_edit_params
from app.services.writing.section_locator import resolve_sections
from app.domain.intent_observation import IntentObservationResult


@pytest.fixture
def writing_state(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.artifact_tools.settings.ARTIFACTS_PATH", str(tmp_path))
    task_id = "rev-task-1"
    art_dir = task_artifact_dir(task_id)
    outline = art_dir / "outline.txt"
    outline.write_text(
        "### 第1章 开端\n\n第一段内容。\n\n第二段对白。\n\n### 第2章 裂痕\n\n第二段在这里。\n",
        encoding="utf-8",
    )
    return {
        "task_id": task_id,
        "session_id": "sess-rev-1",
        "session_turn": 2,
        "manuscript": {"outline_path": "outline.txt", "outline_bytes": 100},
        "mission": {"kind": "writing", "objective": "写整篇剧本"},
        "input_payload": {"goal": "润色一下你的大纲，我认为写的还不错"},
    }


def test_detect_structural_revision(writing_state):
    assert detect_structural_revision(writing_state, writing_state["input_payload"]["goal"]) is True


def test_revision_intent_structural(writing_state):
    rev = infer_revision_intent_structural(writing_state, writing_state["input_payload"]["goal"])
    assert rev is not None
    assert rev["artifact_filename"] == "outline.txt"
    assert rev["revision_scope"] == "full"
    assert rev["completion_policy"] == "stop_after_edit"


def test_section_locator_chapter_paragraph():
    content = "### 第6章 裂痕\n\n第一段。\n\n第二段目标句。\n\n### 第7章\n\n后续。"
    spans = resolve_sections(content, ["第6章 裂痕", "第二段"])
    assert spans
    start, end, anchor = spans[0]
    assert start >= 1
    assert end >= start


def test_scoped_read(writing_state):
    out = handle_read_text_artifact(
        {
            "task_id": writing_state["task_id"],
            "filename": "outline.txt",
            "start_line": 1,
            "end_line": 3,
            "max_chars": 8000,
        }
    )
    assert out["status"] == "ok"
    assert out.get("scope") == {"start_line": 1, "end_line": 3}
    assert "第1章" in out["raw_content"]


def test_revision_intent_to_edit_params():
    ri = RevisionIntent(
        artifact_filename="outline.txt",
        edits=[RevisionEdit(old_text="第二段", new_text="第二段（润色）")],
    )
    params = revision_intent_to_edit_params(ri, "t1")
    assert params["old_text"] == "第二段"
    assert params["filename"] == "outline.txt"


def test_derive_planning_required_revision_executable(writing_state):
    rev = infer_revision_intent_structural(writing_state, "润色第1章")
    obs = IntentObservationResult(
        is_revision=True,
        revision_intent=rev,
        intent_kind="writing",
    )
    state = {
        **writing_state,
        "intent_observation": obs.to_dict(),
        "intent_snapshot": {
            "task_id": writing_state["task_id"],
            "session_turn": 2,
            "user_input_hash": hash_user_input("润色第1章"),
            "intent_kind": "writing",
            "target_mode": "manuscript_mode",
            "turn_kind_candidate": "steer_execute",
            "is_revision": True,
            "revision_intent": rev,
            "planning_required": False,
            "snapshot_status": "frozen",
            "created_at": "2026-01-01T00:00:00Z",
        },
    }
    required, source = derive_planning_required(state)  # type: ignore[arg-type]
    assert required is False
    assert source == "rule:revision_executable"


def test_intent_snapshot_freeze_and_reuse(writing_state):
    result = IntentObservationResult(
        intent_kind="writing",
        target_mode="manuscript_mode",
        is_revision=True,
        revision_intent={"artifact_filename": "outline.txt", "target_sections": ["第1章"]},
        needs_planning=False,
    )
    state = freeze_intent_snapshot(
        writing_state,
        result,
        planning_required=False,
        planning_required_source="rule:revision_executable",
    )
    cached = get_or_freeze_intent_snapshot(state)  # type: ignore[arg-type]
    assert cached is not None
    assert cached.is_revision is True


def test_event_classification_revision(writing_state):
    ev = classify_user_event(writing_state, payload={"goal": "润色一下大纲"})
    assert ev.event_type == "revision"


def test_mission_rebound_drift(writing_state):
    writing_state["intent_snapshot"] = {
        "task_id": writing_state["task_id"],
        "session_turn": 2,
        "user_input_hash": "abc",
        "intent_kind": "writing",
        "target_mode": "manuscript_mode",
        "turn_kind_candidate": None,
        "is_revision": True,
        "revision_intent": {
            "artifact_filename": "outline.txt",
            "operation_type": "polish",
            "revision_scope": "chapter",
            "target_sections": ["第1章"],
        },
        "planning_required": False,
        "snapshot_status": "frozen",
        "created_at": "2026-01-01",
    }
    drift = detect_task_drift(writing_state)
    assert drift.get("drift_type") == "mission_rebound"


def test_is_revision_done(writing_state):
    writing_state["intent_observation"] = {"is_revision": True}
    writing_state["intent_snapshot"] = {
        "task_id": writing_state["task_id"],
        "session_turn": 2,
        "user_input_hash": "x",
        "intent_kind": "writing",
        "target_mode": "manuscript_mode",
        "turn_kind_candidate": None,
        "is_revision": True,
        "revision_intent": {
            "artifact_filename": "outline.txt",
            "edits": [{"old_text": "第二段", "new_text": "改"}],
            "completion_policy": "stop_after_edit",
        },
        "planning_required": False,
        "snapshot_status": "frozen",
        "created_at": "2026-01-01",
    }
    writing_state["tool_results"] = [
        {
            "tool": "edit_text_artifact",
            "status": "ok",
            "result": {"replacements": 1, "selection": {"scope": {"start_line": None}}},
        }
    ]
    assert is_revision_turn(writing_state) is True
    done, reason = is_revision_done(writing_state)
    assert done is True
    assert reason == "revision_done"


def test_revision_intent_executable_with_sections():
    assert revision_intent_executable({"artifact_filename": "a.txt", "target_sections": ["第1章"]})


def test_revision_intent_executable_full_scope():
    assert revision_intent_executable({"artifact_filename": "outline.txt", "revision_scope": "full"})


def test_classify_revision_override_with_artifacts(writing_state):
    assert classify_revision_override(writing_state["input_payload"], writing_state) is True


def test_revision_edit_plot_contract_excludes_mission_runtime():
    contract = revision_edit_plot_contract()
    assert contract["primary_op"] == "edit_plot"
    assert "mission_runtime" in contract["forbid"]


def test_continuation_merge(writing_state):
    writing_state["input_payload"]["last_revision_intent"] = {
        "artifact_filename": "outline.txt",
        "target_sections": ["第1章"],
        "revision_scope": "chapter",
    }
    merged = merge_continuation_revision_intent(
        writing_state,
        "上一版不错，把结尾再收一点",
        {"operation_type": "polish", "target_sections": []},
    )
    assert merged is not None
    assert merged["target_sections"] == ["第1章"]
    assert is_revision_continuation_goal("上一版不错") is True


def test_scope_confidence_low_without_sections():
    assert scope_resolution_confidence({"artifact_filename": "a.txt"}) < 0.5
    assert needs_revision_clarification({"artifact_filename": "a.txt"}) is True


def test_revision_boundary_text():
    text = build_revision_boundary_text(
        {
            "artifact_filename": "outline.txt",
            "artifact_role": "outline",
            "revision_scope": "chapter",
            "target_sections": ["第1章"],
            "operation_type": "polish",
            "constraints": ["no_expand"],
            "output_mode": "diff",
            "completion_policy": "stop_after_edit",
        }
    )
    assert "第1章" in text
    assert "no_expand" in text


def test_mark_revision_completed_event(writing_state):
    writing_state["intent_observation"] = {"is_revision": True}
    updated = mark_revision_completed(writing_state)  # type: ignore[arg-type]
    assert updated["input_payload"].get("revision_done") is True
    events = (updated.get("turn_event_log") or {}).get("events") or updated.get("turn_events")
    if events:
        assert any(e.get("event_type") == "revision_done" for e in events)

