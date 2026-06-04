from app.runtime.router import _writing_route_allowed
from app.runtime.state import create_initial_state, merge_state
from app.services.mode_execution import should_route_mission_writing_mode


def test_qa_mode_blocks_writing_route():
    state = create_initial_state(
        task_id="rt-qa",
        input_payload={
            "target_mode": "qa_mode",
            "writing_intent": {"enabled": True},
        },
    )
    assert _writing_route_allowed(state) is False


def test_manuscript_mission_route_when_contract_matches():
    state = create_initial_state(
        task_id="rt-ms",
        input_payload={
            "target_mode": "manuscript_mode",
            "execution_path": "mission_writing",
            "execution_mode": "mission",
            "mission": {"kind": "writing"},
            "writing_intent": {"enabled": True},
        },
    )
    state = merge_state(state, mission={"kind": "writing"})
    assert should_route_mission_writing_mode(state) is True
