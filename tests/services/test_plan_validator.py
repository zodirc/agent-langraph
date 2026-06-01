from app.runtime.state import create_initial_state, merge_state
from app.services.plan_validator import validate_plan


def test_validate_plan_rejects_vague_steps():
    state = create_initial_state(task_id="t1")
    payload = {"goal": "写报告", "risk_level": "LOW"}
    plan = ["分析", "思考", "准备"]
    result = validate_plan(plan, [], payload, state)
    assert not result.valid
    assert result.should_replan
    assert any("plan_too_vague" in issue for issue in result.issues)


def test_validate_plan_rejects_unknown_tool():
    state = create_initial_state(task_id="t1")
    payload = {"goal": "计算", "risk_level": "LOW"}
    plan = ["run calculator"]
    result = validate_plan(plan, ["nonexistent_tool_xyz"], payload, state)
    assert not result.valid
    assert result.should_replan
    assert any("unknown_tool" in issue for issue in result.issues)


def test_validate_plan_accepts_concrete_plan():
    state = create_initial_state(task_id="t1")
    payload = {"goal": "echo test", "risk_level": "LOW"}
    plan = ["invoke echo with user goal"]
    result = validate_plan(plan, ["echo"], payload, state)
    assert result.valid
    assert not result.should_replan


def test_validate_plan_contract_forbid():
    state = create_initial_state(task_id="t1")
    payload = {
        "goal": "edit only",
        "turn_contract": {"forbid": ["append_body"]},
    }
    plan = ["append_body chapter 1"]
    result = validate_plan(plan, [], payload, state)
    assert not result.valid
    assert any("plan_conflicts_contract" in issue for issue in result.issues)
