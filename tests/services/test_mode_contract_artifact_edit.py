from app.runtime.state import create_initial_state, merge_state
from app.services.mode_resolution import apply_mode_contract_to_state
from app.services.mode_router import resolve_target_mode


def test_qa_mode_artifact_edit_profile_keeps_artifact_tools():
    state = create_initial_state(
        task_id="mc-artifact-edit-1",
        input_payload={
            "goal": "润色一下",
            "thin_execution_profile": "artifact_edit",
            "writing_intent": {"enabled": True, "source": "artifact_edit"},
            "route_audit": {"inferred_kind": "qa", "kind_confidence": 0.7},
        },
    )
    state = merge_state(
        state,
        selected_tools=["read_text_artifact", "write_text_artifact", "calculator"],
    )
    res = resolve_target_mode(state)
    updated = apply_mode_contract_to_state(state, res)
    payload = updated.get("input_payload") or {}
    tools = updated.get("selected_tools") or []
    assert (payload.get("writing_intent") or {}).get("enabled") is True
    assert "read_text_artifact" in tools
    assert "write_text_artifact" in tools
    assert "calculator" in tools
