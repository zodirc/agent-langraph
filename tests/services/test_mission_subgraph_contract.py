"""Mission subgraph must not force append_body when turn contract forbids writing."""

from app.runtime.state import merge_state
from app.services.mission_executor import run_subgraph_writing


def test_subgraph_writing_defers_to_pipeline_when_contract_blocks(base_state, monkeypatch):
    calls: list[str] = []

    def fake_pipeline(state):
        calls.append("pipeline")
        return merge_state(state, status="TOOL_EXECUTED")

    monkeypatch.setattr(
        "app.services.mission_executor.run_pipeline_request",
        fake_pipeline,
    )
    monkeypatch.setattr(
        "app.services.mission_executor.writing_node",
        lambda s: merge_state(s, status="WRITTEN"),
    )

    state = merge_state(
        base_state,
        mission={"kind": "writing", "step_policy": {"chars_per_step": 4000}},
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {
                "primary_op": "edit_plot",
                "override_step_policy": True,
                "forbid": ["append_body"],
                "tools": ["read_text_artifact", "edit_text_artifact"],
            },
            "writing_intent": {"enabled": False, "action": "edit_plot"},
        },
    )
    from app.services.mission_executor import _run_contract_tool_step

    tool_out = _run_contract_tool_step(state)
    assert tool_out is not None

    run_subgraph_writing(state)
    assert calls == []
