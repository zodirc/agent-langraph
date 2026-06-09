"""Integration: revision thin planning → tool_node fast path."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.nodes.planning_node import planning_node
from app.nodes.tool_node import tool_execution_node
from app.domain.intent_observation import IntentObservationResult
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.artifact_tools import task_artifact_dir
from app.services.intent_snapshot import freeze_intent_snapshot
from app.services.revision_detection import infer_revision_intent_structural


@pytest.fixture
def revision_outline_state(tmp_path, monkeypatch, isolated_stores):
    monkeypatch.setattr("app.services.artifact_tools.settings.ARTIFACTS_PATH", str(tmp_path))
    task_id = "rev-fast-path-1"
    art_dir = task_artifact_dir(task_id)
    outline = art_dir / "outline.txt"
    outline.write_text(
        "### 第1章 开端\n\n第一段内容。\n\n第二段对白。\n\n### 第2章 裂痕\n\n第二段在这里。\n",
        encoding="utf-8",
    )
    goal = "润色第1章"
    rev = infer_revision_intent_structural(
        {
            "task_id": task_id,
            "manuscript": {"outline_path": "outline.txt", "outline_bytes": 100},
        },
        goal,
    )
    assert rev is not None
    base = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": goal,
            "interaction_mode": "writing",
            "risk_level": "LOW",
        },
    )
    observation = IntentObservationResult(
        is_revision=True,
        revision_intent=rev,
        intent_kind="writing",
        target_mode="manuscript_mode",
        turn_kind_candidate="steer_execute",
        needs_planning=False,
        source="structural",
    )
    state = merge_state(
        base,
        session_turn=2,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 100},
        mission={"kind": "writing", "objective": "写整篇剧本"},
        intent_observation=observation.to_dict(),
    )
    return freeze_intent_snapshot(
        state,
        observation,
        planning_required=False,
        planning_required_source="rule:revision_executable",
    )


@patch("app.services.llm_client.invoke_structured")
def test_planning_revision_thin_skip_avoids_planning_llm(
    mock_invoke,
    revision_outline_state,
):
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")

    out = planning_node(revision_outline_state)
    mock_invoke.assert_not_called()

    payload = out.get("input_payload") or {}
    assert payload.get("thin_execution_profile") == "revision_scoped"
    assert out.get("selected_tools") == ["read_text_artifact", "edit_text_artifact"]
    assert out.get("status") == TaskStatus.PLANNED.value
    audits = [e for e in (out.get("audit_log") or []) if e.get("action") == "revision_thin_skip"]
    assert audits
    assert payload.get("planning_required_source") in (None, "rule:revision_executable") or (
        "revision" in str(payload.get("planning_required_source") or "")
    )


@patch("app.services.outline_steer_patch.plan_edit_from_read_content")
def test_tool_revision_fast_path_completes_edit(
    mock_plan,
    revision_outline_state,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.artifact_tools.settings.ARTIFACTS_PATH", str(tmp_path))
    mock_plan.return_value = {
        "found": True,
        "old_text": "第二段对白。",
        "new_text": "第二段对白（润色）。",
    }

    planned = merge_state(
        revision_outline_state,
        input_payload={
            **revision_outline_state["input_payload"],
            "pre_planning_completed": True,
            "thin_execution_profile": "revision_scoped",
            "revision_intent": revision_outline_state["intent_observation"]["revision_intent"],
            "planning_required_source": "rule:revision_executable",
        },
        selected_tools=["read_text_artifact", "edit_text_artifact"],
        tool_stages=[["read_text_artifact"], ["edit_text_artifact"]],
        status=TaskStatus.PLANNED.value,
    )

    executed = tool_execution_node(planned)
    payload = executed.get("input_payload") or {}
    assert payload.get("revision_done") is True
    assert executed.get("status") == TaskStatus.TOOL_EXECUTED.value

    tool_results = executed.get("tool_results") or []
    tools_run = [r.get("tool") for r in tool_results]
    assert "read_text_artifact" in tools_run
    assert "edit_text_artifact" in tools_run
    apply_edits = [
        r for r in tool_results if r.get("tool") == "edit_text_artifact" and r.get("phase") == "apply"
    ]
    assert apply_edits
    assert int((apply_edits[0].get("result") or {}).get("replacements") or 0) >= 1

    events = (executed.get("turn_event_log") or {}).get("events") or executed.get("turn_events") or []
    event_types = [e.get("event_type") for e in events]
    assert "scoped_read" in event_types
    assert "scoped_edit_completed" in event_types
    assert "revision_done" in event_types


@patch("app.services.outline_steer_patch.plan_edit_from_read_content")
@patch("app.services.llm_client.invoke_structured")
def test_revision_full_outline_polish_end_to_end(
    mock_invoke,
    mock_plan,
    tmp_path,
    monkeypatch,
    isolated_stores,
):
    """Generic outline polish (debug.log scenario) uses revision fast path."""
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")
    monkeypatch.setattr("app.services.artifact_tools.settings.ARTIFACTS_PATH", str(tmp_path))

    task_id = "rev-full-outline-1"
    art_dir = task_artifact_dir(task_id)
    (art_dir / "outline.txt").write_text(
        "### 大纲\n\n第一段。\n\n第二段。\n",
        encoding="utf-8",
    )
    goal = "润色一下你的大纲，我认为写的还不错"
    rev = infer_revision_intent_structural(
        {
            "task_id": task_id,
            "manuscript": {"outline_path": "outline.txt", "outline_bytes": 50},
        },
        goal,
    )
    assert rev is not None
    assert rev["revision_scope"] == "full"

    base = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": goal,
            "interaction_mode": "writing",
            "risk_level": "LOW",
        },
    )
    observation = IntentObservationResult(
        is_revision=True,
        revision_intent=rev,
        intent_kind="writing",
        target_mode="manuscript_mode",
        turn_kind_candidate="steer_execute",
        needs_planning=False,
        source="structural",
    )
    state = merge_state(
        base,
        session_turn=2,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 50},
        mission={"kind": "writing", "objective": "写整篇剧本"},
    )
    state = freeze_intent_snapshot(
        state,
        observation,
        planning_required=False,
        planning_required_source="rule:revision_executable",
    )
    planned = planning_node(state)
    mock_invoke.assert_not_called()
    assert any(
        e.get("action") == "revision_thin_skip" for e in (planned.get("audit_log") or [])
    )

    mock_plan.return_value = {
        "found": True,
        "old_text": "第二段。",
        "new_text": "第二段（润色）。",
    }
    executed = tool_execution_node(planned)
    assert (executed.get("input_payload") or {}).get("revision_done") is True
