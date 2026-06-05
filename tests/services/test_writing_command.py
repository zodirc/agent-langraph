"""Regression tests: Intent → Command Builder → Confirmation → Executor."""

import pytest

from app.domain.writing_command import WritingCommand
from app.domain.writing_intent_model import IntentAnchor, WritingIntentRecord
from app.runtime.state import merge_state
from app.services.confirmation.preview_resolver import resolve_outcome_preview
from app.services.turn_contract import planning_fallback_from_state
from app.services.writing.command_builder import build_from_intent, build_writing_command
from app.services.writing.command_validator import (
    command_retry_blocked,
    validate_confirmation,
    validate_executable,
    validate_target_bound,
)
from app.services.writing.executor import block_command_execution
from app.services.writing.intent_parser import store_intent_on_payload
from app.services.writing.state_machine import enqueue_command, get_current_command


@pytest.fixture
def writing_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_tools.task_artifact_dir",
        lambda task_id: tmp_path / task_id,
    )
    task_id = "wc-task"
    art = tmp_path / task_id
    art.mkdir(parents=True)
    outline = "第一章 主角出身\n第二章 能力设定\n"
    body = "正文第一章\n" * 20
    (art / "outline.txt").write_text(outline, encoding="utf-8")
    (art / "novel.txt").write_text(body, encoding="utf-8")
    return merge_state(
        {
            "task_id": task_id,
            "session_id": "sess-wc",
            "mission": {
                "kind": "writing",
                "step_policy": {"outline_artifact": "outline.txt", "body_artifact": "novel.txt"},
            },
            "manuscript": {
                "outline_path": "outline.txt",
                "outline_bytes": len(outline.encode()),
                "body_path": "novel.txt",
                "body_bytes": len(body.encode()),
            },
            "input_payload": {},
        },
    )


def _intent(action: str, **anchor) -> WritingIntentRecord:
    return WritingIntentRecord(action=action, anchor=IntentAnchor(**anchor))


def test_edit_plot_preview_uses_outline_not_body(writing_state):
    intent = _intent("edit_plot")
    payload = store_intent_on_payload({}, intent)
    payload["writing_command"] = {
        "action": "edit_plot",
        "target_filename": "outline.txt",
        "command_id": "c1",
    }
    state = merge_state(writing_state, input_payload=payload)
    item = {"id": "wi-edit", "kind": "edit_plot", "params": {"command_id": "c1"}}
    preview = resolve_outcome_preview(state, item)
    assert preview.filename == "outline.txt"
    assert "主角出身" in preview.content


def test_edit_plot_preview_explicit_body_target(writing_state):
    intent = _intent("edit_plot", old_text="正文第一章", target_hint="body")
    payload = store_intent_on_payload({}, intent)
    command = build_from_intent(writing_state, intent)
    payload = {**payload, "writing_command": command.to_dict()}
    state = merge_state(writing_state, input_payload=payload)
    item = {"id": "wi-edit-body", "kind": "edit_plot", "params": {"command_id": command.command_id}}
    preview = resolve_outcome_preview(state, item)
    assert preview.filename == "novel.txt"


def test_build_edit_plot_command_outline_default(writing_state):
    command = build_from_intent(writing_state, _intent("edit_plot"))
    assert command.target_filename == "outline.txt"
    assert command.target_kind == "outline"


def test_build_edit_plot_command_explicit_body(writing_state):
    command = build_from_intent(writing_state, _intent("edit_plot", target_hint="body"))
    assert command.target_filename == "novel.txt"
    assert command.target_kind == "body"


def test_edit_outline_without_old_text(writing_state):
    command = build_from_intent(writing_state, _intent("edit_plot"))
    assert command.target_kind == "outline"
    assert not command.edit_spec.get("old_text")


def test_edit_outline_with_exact_anchor(writing_state):
    command = build_from_intent(
        writing_state,
        _intent("edit_plot", old_text="主角出身", new_text="主角身世", target_hint="outline"),
    )
    assert command.edit_spec.get("old_text") == "主角出身"
    assert command.target_filename == "outline.txt"


def test_edit_body_explicit_target(writing_state):
    command = build_from_intent(
        writing_state,
        _intent("edit_plot", old_text="正文第一章", target_hint="body"),
    )
    assert command.target_kind == "body"
    assert command.target_filename == "novel.txt"


def test_unconfirmed_edit_plot_blocked(writing_state):
    intent = _intent("edit_plot", target_hint="outline")
    payload = store_intent_on_payload(
        {"steer_applied_at": "2026-06-05T00:00:00Z", "steer_intent_pending_confirm": True},
        intent,
    )
    state = merge_state(writing_state, input_payload=payload)
    command = build_writing_command(state, {"kind": "edit_plot"})
    err = validate_executable(command, state["input_payload"])
    assert err is not None
    assert err.code == "confirmation_pending"


def test_confirmed_edit_plot_executable(writing_state):
    intent = _intent("edit_plot", target_hint="outline")
    payload = store_intent_on_payload(
        {
            "steer_applied_at": "2026-06-05T00:00:00Z",
            "steer_intent_confirmed": True,
        },
        intent,
    )
    state = merge_state(writing_state, input_payload=payload)
    command = build_writing_command(state, {"kind": "edit_plot"})
    assert validate_executable(command, state["input_payload"]) is None


def test_planning_fallback_skips_failed_edit_plot(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing"},
        manuscript={"outline_path": "outline.txt", "outline_bytes": 500},
        input_payload={**base_state["input_payload"], "goal": "改设定"},
        progress={
            "work_plan": {
                "items": [{"kind": "edit_plot", "status": "failed", "id": "wi-fail"}],
            }
        },
    )
    assert planning_fallback_from_state(state) is None


def test_command_retry_blocked_helper():
    payload = {"command_retry_blocked": {"blocked": True, "signature": "edit_plot:outline.txt:"}}
    command = WritingCommand(
        command_id="c1",
        action="edit_plot",
        target_kind="outline",
        target_filename="outline.txt",
    )
    assert command_retry_blocked(payload, command) is True


def test_rejected_confirmation_not_executable():
    command = WritingCommand(
        command_id="cmd-1",
        action="edit_plot",
        target_kind="outline",
        target_filename="outline.txt",
        confirmation_status="rejected",
        requires_confirmation=True,
    )
    err = validate_confirmation(command, {})
    assert err is not None
    assert err.code == "rejected"


def test_resume_keeps_command_id_and_target(writing_state):
    intent = _intent("edit_plot")
    command = build_from_intent(writing_state, intent)
    command = WritingCommand(
        **{**command.to_dict(), "command_id": "cmd-wi-resume"},
    )
    payload = enqueue_command({"steer_applied_at": "t1"}, command)
    bound = get_current_command(payload)
    assert bound.command_id == "cmd-wi-resume"
    assert bound.target_filename == "outline.txt"


def test_review_outline_command(writing_state):
    command = build_from_intent(writing_state, _intent("review_outline"))
    assert command.action == "review_outline"
    assert command.target_filename == "outline.txt"


def test_reset_body_command(writing_state):
    command = build_from_intent(writing_state, _intent("reset_body"))
    assert command.action == "reset_body"
    assert command.target_filename == "novel.txt"


def test_write_outline_command(writing_state):
    command = build_from_intent(writing_state, _intent("write_outline"))
    assert command.action == "write_outline"
    assert command.target_kind == "outline"


def test_blocked_execution_returns_reasoning(writing_state):
    state = merge_state(
        writing_state,
        input_payload={
            "steer_applied_at": "t",
            "steer_intent_pending_confirm": True,
            "steer_intent_confirmed": False,
        },
    )
    command = build_from_intent(state, _intent("edit_plot"))
    err = validate_executable(command, state["input_payload"])
    result = block_command_execution(state, err)
    assert result.get("reasoning_result") or result.get("status")
