"""Planning JSON parse resilience: fallback and empty-stream recovery."""

from app.runtime.state import merge_state
from app.services import llm_client
from app.services.turn_contract import planning_fallback_from_state


def test_planning_fallback_edit_plot_queue(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing", "step_policy": {"then": "append_body"}},
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1200},
        input_payload={
            **base_state["input_payload"],
            "goal": "提升主角出身与能力",
        },
        progress={
            "work_plan": {
                "items": [
                    {"id": "wi-1", "kind": "edit_plot", "status": "pending", "title": "edit plot"},
                ]
            }
        },
    )
    fb = planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("mission_intervention", {}).get("action") == "edit_plot"
    assert "read_text_artifact" in (fb.get("selected_tools") or [])


def test_extract_json_with_repair_planning_state_fallback(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing"},
        manuscript={"outline_bytes": 500, "outline_path": "outline.txt"},
        input_payload={**base_state["input_payload"], "goal": "改设定"},
        progress={
            "work_plan": {
                "items": [{"kind": "edit_plot", "status": "pending"}],
            }
        },
    )

    original = llm_client._extract_json
    llm_client._extract_json = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
    try:
        result = llm_client.extract_json_with_repair(
            "planning",
            "",
            prefer_keys=("plan",),
            trace_state=state,
        )
    finally:
        llm_client._extract_json = original

    assert result.get("parser_fallback") is True
    assert result.get("mission_intervention", {}).get("action") == "edit_plot"
