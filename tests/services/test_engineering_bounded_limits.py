from unittest.mock import patch

from app.runtime.state import create_initial_state
from app.services.engineering_execution import EngineeringStepBudget, run_engineering_bounded
from app.services.project_verify.backends import ProjectVerifyResult


def test_step_budget_blocks_when_exhausted():
    budget = EngineeringStepBudget(max_steps=2)
    assert budget.consume("a")
    assert budget.consume("b")
    assert not budget.consume("c")
    assert budget.exhausted()


@patch("app.services.engineering_execution.verify_project")
@patch("app.services.engineering_execution._generate_files_via_llm")
def test_max_steps_stops_before_verify(mock_gen, mock_verify):
    mock_gen.return_value = {
        "summary": "x",
        "preview": "p",
        "files": [{"path": "main.cpp", "content": "int main(){}"}],
    }
    state = create_initial_state(
        task_id="eng-steps-1",
        input_payload={
            "goal": "cpp",
            "intent_kind": "code",
            "route_audit": {"inferred_kind": "code"},
        },
    )
    with patch("app.services.engineering_execution.get_mode_contract") as mock_contract:
        from app.services.mode_registry import ModeContract, ModeDelivery, ModeExecution, ModeGuards, ModeSecurity, ModeVerify

        mock_contract.return_value = ModeContract(
            mode_id="engineering_mode",
            execution=ModeExecution(path="engineering_bounded", max_steps=3, max_repair_attempts=3),
            delivery=ModeDelivery(primary="tool_write"),
            verify=ModeVerify(enabled=True),
            guards=ModeGuards(),
            security=ModeSecurity(),
        )
        result = run_engineering_bounded(state)
    mock_verify.assert_not_called()
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("steps_used", 0) <= 3
    assert "verify_skipped_budget" in str(trace.get("verify_result", {}))


@patch("app.services.engineering_execution.verify_project")
@patch("app.services.engineering_execution._generate_files_via_llm")
@patch("app.services.engineering_execution.minimal_repair_files", return_value=False)
def test_max_repair_attempts_respected(mock_minimal, mock_gen, mock_verify):
    mock_gen.return_value = {
        "summary": "x",
        "preview": "p",
        "files": [{"path": "main.cpp", "content": "int x;"}],
    }
    mock_verify.return_value = ProjectVerifyResult(
        ok=False,
        backend="cpp",
        status="failed",
        stderr="error: expected '}'",
        issues=["compile_failed"],
    )
    state = create_initial_state(
        task_id="eng-repair-1",
        input_payload={"goal": "cpp fix", "intent_kind": "code"},
    )
    with patch("app.services.engineering_execution.get_mode_contract") as mock_contract:
        from app.services.mode_registry import ModeContract, ModeDelivery, ModeExecution, ModeGuards, ModeSecurity, ModeVerify

        mock_contract.return_value = ModeContract(
            mode_id="engineering_mode",
            execution=ModeExecution(
                path="engineering_bounded", max_steps=20, max_repair_attempts=2
            ),
            delivery=ModeDelivery(primary="tool_write"),
            verify=ModeVerify(enabled=True),
            guards=ModeGuards(),
            security=ModeSecurity(),
        )
        result = run_engineering_bounded(state)
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("repair_attempts", 0) <= 2
