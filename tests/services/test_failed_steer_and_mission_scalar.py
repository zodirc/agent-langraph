"""Regression: scalar payload.mission + FAILED steer must not resume or crash tools."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.artifact_resolver import build_artifact_manifest
from app.services.control_payload import merge_stripped_message_payload
from app.services.event_classification import resolve_inbound_event
from app.services.mission_schema import build_mission_dict
from app.services.session_turn import build_inbound_merged_payload
from app.services.session_fsm import get_fsm_state, sync_fsm_state


def test_build_artifact_manifest_tolerates_scalar_mission():
    manifest = build_artifact_manifest(
        "t-scalar",
        payload={"mission": "writing"},
    )
    assert isinstance(manifest, list)


def test_failed_steer_routes_to_redirect_not_resume():
    base = create_initial_state(user_id="u", task_type="qa")
    mission = build_mission_dict(base, {"mission": {"kind": "writing"}}, kind="writing")
    state = merge_state(
        base,
        session_turn=2,
        status=TaskStatus.FAILED.value,
        input_payload={
            "fsm_state": "RUNNING",
            "mission": mission,
            "execution_mode": "mission",
        },
    )
    corrupted_ip = dict(state.get("input_payload") or {})
    corrupted_ip["mission"] = "writing"
    state = merge_state(state, input_payload=corrupted_ip)
    state = sync_fsm_state(state)
    text = "我只是想要一篇散文，5000字即可，不需要这么多内容"
    payload = merge_stripped_message_payload(state.get("input_payload"), text=text)
    inbound = build_inbound_merged_payload(state, payload)
    event = resolve_inbound_event(state, payload=inbound)
    assert event.event_type == "redirect"
    assert inbound.get("turn_policy_decision", {}).get("intent") == "supersede_active_mission"
    assert get_fsm_state(merge_state(state, input_payload=inbound)) in ("REPLANNING", "IDLE")
