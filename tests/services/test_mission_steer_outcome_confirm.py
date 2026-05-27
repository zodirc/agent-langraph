"""Steer outcome confirmation after material work items (item 2)."""

import pytest

from app.runtime.state import merge_state
from app.services.mission_steer_outcome_confirm import (
    apply_outcome_confirmation_after_work_item,
    apply_steer_outcome_confirmation_pending,
    build_steer_outcome_summary,
    confirm_steer_outcome,
    steer_outcome_confirmation_pending,
    steer_outcome_confirmation_required,
)


@pytest.fixture
def outline_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_tools.task_artifact_dir",
        lambda task_id: tmp_path / task_id,
    )
    task_id = "task-outcome-1"
    art = tmp_path / task_id
    art.mkdir(parents=True)
    (art / "outline.txt").write_text("第一章 岁月如梭\n" * 30, encoding="utf-8")
    return merge_state(
        {
            "task_id": task_id,
            "session_id": "sess-1",
            "mission": {
                "kind": "writing",
                "step_policy": {"outline_artifact": "outline.txt"},
            },
            "manuscript": {"outline_path": "outline.txt", "outline_bytes": 500},
            "observation": {"has_failures": False},
            "input_payload": {
                "steer_applied_at": "2026-05-26T00:00:00Z",
                "steer_watch_outcome": True,
                "mission_intervention": {"action": "rewrite_outline", "force": True},
            },
        },
    )


def test_outcome_required_after_write_outline(outline_state):
    item = {"id": "wi-1", "kind": "write_outline", "title": "write_outline"}
    assert steer_outcome_confirmation_required(outline_state, item) is True


def test_outcome_not_required_without_steer_watch(outline_state):
    payload = dict(outline_state["input_payload"])
    payload.pop("steer_applied_at", None)
    payload.pop("steer_watch_outcome", None)
    payload.pop("mission_intervention", None)
    state = merge_state(outline_state, input_payload=payload)
    item = {"id": "wi-1", "kind": "write_outline", "title": "write_outline"}
    assert steer_outcome_confirmation_required(state, item) is False


def test_build_summary_contains_excerpt(outline_state):
    item = {"id": "wi-1", "kind": "write_outline", "title": "write_outline"}
    summary = build_steer_outcome_summary(outline_state, item)
    assert "outline.txt" in summary["summary_text"]
    assert "岁月" in summary["artifact_excerpt"]


def test_apply_outcome_sets_pending(outline_state):
    item = {"id": "wi-1", "kind": "write_outline", "title": "write_outline"}
    updated = apply_outcome_confirmation_after_work_item(outline_state, item)
    assert steer_outcome_confirmation_pending(updated["input_payload"])
    assert updated.get("final_answer")


def test_confirm_clears_pending(outline_state):
    payload = apply_steer_outcome_confirmation_pending(
        outline_state["input_payload"],
        {"summary_text": "节选", "work_item_id": "wi-1"},
        task_id=outline_state["task_id"],
    )
    state = merge_state(outline_state, input_payload=payload)
    confirmed = confirm_steer_outcome(state)
    assert not steer_outcome_confirmation_pending(confirmed["input_payload"])
    assert confirmed["input_payload"].get("steer_outcome_confirmed_for") == "wi-1"
