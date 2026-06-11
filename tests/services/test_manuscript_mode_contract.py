from app.runtime.state import create_initial_state, merge_state
from app.services.mode_resolution import apply_mode_contract_to_state
from app.services.mode_router import resolve_target_mode


def test_manuscript_mode_contract_applies_artifact_tools():
    state = create_initial_state(
        task_id="mc-manuscript-1",
        input_payload={
            "goal": "续写下一章",
            "route_audit": {"inferred_kind": "manuscript", "kind_confidence": 0.8},
        },
    )
    state = merge_state(
        state,
        selected_tools=["write_text_artifact", "mkdir_path", "read_text_artifact"],
    )
    res = resolve_target_mode(state)
    assert res.target_mode == "manuscript_mode"
    updated = apply_mode_contract_to_state(state, res)
    tools = updated.get("selected_tools") or []
    assert "write_text_artifact" in tools
    assert "read_text_artifact" in tools
    assert "mkdir_path" not in tools
    payload = updated.get("input_payload") or {}
    assert payload.get("target_mode") == "manuscript_mode"
    assert (payload.get("writing_intent") or {}).get("enabled") is True


def test_manuscript_mode_has_write_action_budget():
    from app.services.mode_registry import get_mode_contract

    contract = get_mode_contract("manuscript_mode")
    assert contract is not None
    assert contract.execution.max_write_actions == 4
