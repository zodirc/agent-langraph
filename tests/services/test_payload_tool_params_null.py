from app.services.mission_intervention import (
    apply_intervention_to_payload,
    normalize_payload_execution_fields,
)


def test_normalize_tool_params_null():
    out = normalize_payload_execution_fields({"tool_params": None, "goal": "x"})
    assert isinstance(out["tool_params"], dict)
    assert out["tool_params"] == {}


def test_apply_intervention_edit_plot_with_null_tool_params():
    payload = apply_intervention_to_payload(
        {"goal": "fix outline", "tool_params": None},
        {
            "action": "edit_plot",
            "force": True,
            "edit_spec": {"filename": "outline.txt"},
        },
    )
    assert isinstance(payload.get("tool_params"), dict)
    assert "read_text_artifact" in payload["tool_params"]
