"""QA thin planning skip policy."""

from app.runtime.state import create_initial_state, merge_state
from app.services.pre_planning import (
    planning_must_run_llm,
    qa_thin_plan,
    run_pre_planning_pipeline,
    should_skip_qa_planning_llm,
)


def test_qa_thin_plan_greeting_label():
    assert qa_thin_plan("你好") == ["respond greeting"]
    assert qa_thin_plan("你能做什么") == ["respond directly"]
    assert qa_thin_plan("请解释 LangGraph 状态机的设计思路") == ["respond directly"]


def test_should_skip_qa_planning_for_greeting():
    state = create_initial_state(input_payload={"goal": "你好", "risk_level": "LOW"})
    state = run_pre_planning_pipeline(state)
    assert should_skip_qa_planning_llm(state) is True


def test_qa_replan_keeps_thin_planning():
    state = create_initial_state(input_payload={"goal": "你好", "route_audit_replan": True})
    state = run_pre_planning_pipeline(state)
    assert planning_must_run_llm(state) is False
    assert should_skip_qa_planning_llm(state) is True


def test_should_not_skip_qa_planning_for_artifact_edit(monkeypatch, tmp_path):
    from tests.conftest import patch_task_artifact_dir

    task_id = "qa-thin-artifact-edit"
    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True)
    (target / "draft.txt").write_text("内容", encoding="utf-8")

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "润色一下"},
    )
    state = run_pre_planning_pipeline(state)
    assert should_skip_qa_planning_llm(state) is False


def test_should_not_skip_qa_planning_for_substantive_followup():
    state = create_initial_state(
        input_payload={"goal": "请详细分析项目架构并给出改进建议"},
    )
    state = merge_state(state, session_turn=2)
    state = run_pre_planning_pipeline(state)
    assert should_skip_qa_planning_llm(state) is False
