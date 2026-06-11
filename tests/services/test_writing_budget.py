from app.services.writing_budget import (
    count_write_actions,
    max_write_actions_for_state,
    write_budget_exhausted,
)


def test_count_write_actions_includes_failed():
    tools = [
        {"tool": "read_text_artifact", "status": "ok"},
        {"tool": "write_text_artifact", "status": "error"},
        {"tool": "edit_text_artifact", "status": "ok"},
    ]
    assert count_write_actions(tools) == 2


def test_write_budget_exhausted_manuscript():
    state = {
        "input_payload": {
            "target_mode": "manuscript_mode",
            "effective_mode_contract": {"execution": {"max_write_actions": 2}},
        }
    }
    tools = [
        {"tool": "write_text_artifact", "status": "ok"},
        {"tool": "append_text_artifact", "status": "ok"},
    ]
    assert max_write_actions_for_state(state) == 2
    assert write_budget_exhausted(state, tools) is True


def test_write_budget_unlimited_when_zero():
    state = {"input_payload": {"target_mode": "qa_mode"}}
    assert max_write_actions_for_state(state) == 0
    assert write_budget_exhausted(state, [{"tool": "write_text_artifact", "status": "ok"}]) is False
