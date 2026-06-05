"""Tests for preemptive foreground execution (epoch + steer classification)."""

import pytest

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.mission_steer import consume_pending_steer
from app.services.artifact_content import SteerPreempted, generate_artifact_content
from app.services.foreground_execution import (
    EpochStale,
    INTERRUPT_P0,
    INTERRUPT_P1,
    INTERRUPT_P2,
    assert_epoch_valid_for_commit,
    bump_foreground_epoch,
    classify_steer_interrupt,
    get_foreground_epoch,
    trigger_foreground_preempt,
)
from app.services.mission_steer import queue_steer_message
from app.services.state_store import get_state_store
from app.services.step_committer import StepCommitter
from app.services.task_control import (
    clear_all_task_control_for_tests,
    register_task_control,
    snapshot_task_control,
)


def setup_function():
    clear_all_task_control_for_tests()


def test_classify_steer_p0_constraint_violation():
    tier = classify_steer_interrupt("不能出现原电影中没有出现的架空人物")
    assert tier == INTERRUPT_P0


def test_classify_steer_p0_material_plot_direction():
    tier = classify_steer_interrupt(
        "你应该基于原电影来编写，人物需要为原电影人物，只是改动原电影的剧情走向"
    )
    assert tier == INTERRUPT_P0


def test_classify_steer_p1_style():
    tier = classify_steer_interrupt("调整风格，语气再口语一点")
    assert tier == INTERRUPT_P1


def test_classify_steer_p2_followup():
    tier = classify_steer_interrupt("这一章主角动机是什么？")
    assert tier == INTERRUPT_P2


def test_bump_foreground_epoch_increments():
    state = create_initial_state(task_id="epoch-task")
    assert get_foreground_epoch(state) == 0
    updated = bump_foreground_epoch(state, reason="test")
    assert get_foreground_epoch(updated) == 1
    assert updated["interrupt_context"]["control_state"] == "INTERRUPT_REQUESTED"


def test_assert_epoch_valid_rejects_stale_step():
    state = bump_foreground_epoch(create_initial_state(task_id="stale-task"))
    with pytest.raises(EpochStale):
        assert_epoch_valid_for_commit(state, step_epoch=0, phase="test")


def test_trigger_foreground_preempt_syncs_task_control():
    state = create_initial_state(task_id="preempt-task")
    register_task_control("preempt-task", "run-1", foreground_epoch=0)
    updated = trigger_foreground_preempt("preempt-task", state, tier=INTERRUPT_P0)
    assert get_foreground_epoch(updated) == 1
    control = snapshot_task_control("preempt-task")
    assert control is not None
    assert control.foreground_epoch == 1
    assert control.pause_requested is True


def test_queue_steer_p0_triggers_preempt(base_state):
    state = {**base_state, "status": TaskStatus.MISSION_RUNNING.value}
    get_state_store().save(state)
    register_task_control(state["task_id"], "run-1", foreground_epoch=0)

    updated = queue_steer_message(
        state["task_id"],
        "不能出现原电影中没有出现的架空人物",
        preempt=False,
    )
    assert get_foreground_epoch(updated) >= 1
    control = snapshot_task_control(state["task_id"])
    assert control is not None
    assert control.pause_requested is True
    payload = updated.get("input_payload") or {}
    assert payload.get("foreground_preempt_consumed") is True
    assert payload.get("require_planning_after_steer") is True


def test_step_committer_rejects_stale_epoch(isolated_stores):
    state = create_initial_state(task_id="commit-epoch")
    isolated_stores.save(state)
    committer = StepCommitter(
        state,
        step_id="wi1_append",
        kind="append_body",
        filename="novel.txt",
        work_item_id="wi1",
        min_chars=10,
        tool_name="append_text_artifact",
    )
    stale_state = bump_foreground_epoch(committer._state)
    committer._state = stale_state
    with pytest.raises(EpochStale):
        committer.commit("Enough content for a valid paragraph here.\n\nSecond para.")


def test_extract_writing_constraints_from_steer():
    from app.services.foreground_execution import extract_writing_constraints

    found = extract_writing_constraints(
        ["不能出现原电影中没有出现的架空人物，按新要求重写"]
    )
    assert found
    assert any("架空人物" in item or "原电影" in item for item in found)


def test_validate_commit_guard_blocks_literal():
    from app.services.foreground_execution import validate_commit_guard

    ok, _ = validate_commit_guard("主角遇见了李逍遥。", ["李逍遥"])
    assert ok is False


def test_consume_pending_steer_enters_replanning(base_state):
    from app.services.execution_control import CONTROL_REPLANNING
    from app.services.foreground_execution import bump_foreground_epoch
    from app.services.graph_run_registry import begin_graph_run
    state = {
        **base_state,
        "status": TaskStatus.MISSION_RUNNING.value,
        "mission": {"kind": "writing", "total_target_chars": 50000},
        "input_payload": {
            "goal": "写暗战小说",
            "current_work_item": {"id": "wi1", "kind": "append_body"},
            "execution_grant": {"consume_once": True},
        },
    }
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(
        state,
        execution_run={"run_id": run_id},
        pending_user_message={
            "messages": [
                {
                    "message": "不能出现原电影中没有出现的架空人物",
                    "queued_at": "2026-01-01T00:00:00Z",
                    "preempt": True,
                    "priority": 100,
                }
            ]
        },
    )
    state = bump_foreground_epoch(state, reason="test_preempt")
    get_state_store().save(state)

    updated = consume_pending_steer(state)
    ctx = updated.get("interrupt_context") or {}
    assert ctx.get("control_state") == CONTROL_REPLANNING
    payload = updated.get("input_payload") or {}
    assert payload.get("steer_replan_mode") in ("rewrite", "repair")
    assert payload.get("require_planning_after_steer") is True
    assert payload.get("current_work_item") is None
    assert payload.get("execution_grant") is None
    assert updated.get("pending_user_message") is None


def test_generate_artifact_content_raises_on_epoch_preempt(base_state):
    state = {**base_state, "status": TaskStatus.MISSION_RUNNING.value}
    get_state_store().save(state)
    register_task_control(state["task_id"], "run-1", foreground_epoch=0)
    queue_steer_message(
        state["task_id"],
        "停止，按我的新要求重写",
    )
    try:
        generate_artifact_content(
            state=state,
            tool_name="append_text_artifact",
            filename="novel.txt",
            goal="继续写",
            target_chars=200,
        )
        assert False, "expected SteerPreempted"
    except SteerPreempted:
        assert True
