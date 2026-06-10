from app.runtime.state import create_initial_state, merge_state
from app.services.mode_resolution import apply_mode_contract_to_state, run_mode_resolution_pipeline
from app.services.mode_router import ModeResolution, resolve_target_mode


def test_qa_mode_contract_blocks_writing_and_mission_legacy(monkeypatch):
    import app.config.settings as settings_module

    monkeypatch.setattr(settings_module.settings, "QA_MODE_TOOLS_RESIDENT", False)
    state = create_initial_state(
        task_id="mc-qa-1",
        input_payload={
            "goal": "为什么这样设计？",
            "route_audit": {"inferred_kind": "qa", "kind_confidence": 0.7},
            "writing_intent": {"enabled": True, "action": "write_body"},
            "mission": {"kind": "writing"},
        },
    )
    state = merge_state(state, selected_tools=["write_text_artifact", "calculator"])
    res = resolve_target_mode(state)
    assert res.target_mode == "qa_mode"
    updated = apply_mode_contract_to_state(state, res)
    payload = updated.get("input_payload") or {}
    assert payload.get("target_mode") == "qa_mode"
    assert (payload.get("writing_intent") or {}).get("enabled") is False
    assert updated.get("mission") is None
    assert "write_text_artifact" not in (updated.get("selected_tools") or [])


def test_qa_mode_contract_resident_filters_engineering_keeps_artifacts():
    state = create_initial_state(
        task_id="mc-qa-resident-1",
        input_payload={
            "goal": "润色一下",
            "route_audit": {"inferred_kind": "qa", "kind_confidence": 0.7},
            "writing_intent": {"enabled": True, "source": "artifact_edit"},
            "thin_execution_profile": "artifact_edit",
        },
    )
    state = merge_state(
        state,
        selected_tools=[
            "read_text_artifact",
            "write_text_artifact",
            "mkdir_path",
            "verify_backend",
        ],
    )
    res = resolve_target_mode(state)
    updated = apply_mode_contract_to_state(state, res)
    tools = updated.get("selected_tools") or []
    assert "read_text_artifact" in tools
    assert "write_text_artifact" in tools
    assert "mkdir_path" not in tools
    assert "verify_backend" not in tools
    payload = updated.get("input_payload") or {}
    assert (payload.get("route_audit") or {}).get("writing_blocked") is False


def test_manuscript_to_engineering_isolate():
    state = create_initial_state(
        task_id="mc-iso-1",
        input_payload={
            "goal": "做一个 2048 游戏",
            "current_mode": "manuscript_mode",
            "route_audit": {"inferred_kind": "interactive_app", "kind_confidence": 0.85},
            "mission": {"kind": "writing"},
        },
    )
    state = merge_state(state, mission={"kind": "writing"})
    updated = run_mode_resolution_pipeline(state)
    payload = updated.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode"
    assert payload.get("mode_switch_action") == "isolate"
    assert updated.get("mission") is None
