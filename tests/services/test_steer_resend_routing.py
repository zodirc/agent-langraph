"""Steer resend / mid-mission correction must replan, not mechanical append."""

from app.runtime.state import TaskStatus, merge_state
from app.services.event_classification import classify_user_event
from app.services.confirmation.gate_registry import GateContext, intent_gate_required
from app.services.mission_steer import planning_steer_replan_active, steer_requires_planning
from app.services.session.turn_policy import resolve_session_turn


def test_turn_policy_resend_with_active_mission_is_supersede(base_state):
    goal = "写一份电影剧本，谍战剧情，要包括细节，民国背景"
    mission = {"kind": "writing", "objective": goal}
    state = merge_state(base_state, mission=mission, session_turn=2)
    decision = resolve_session_turn(
        state,
        {"goal": goal, "meta": {"resend": True}},
        goal,
        incoming={"goal": goal, "meta": {"resend": True}},
    )
    assert decision.intent == "supersede_active_mission"
    assert decision.source == "user_resend"


def test_turn_policy_resend_without_mission_is_isolate_qa(base_state):
    goal = "写一份电影剧本，谍战剧情，要包括细节，民国背景"
    decision = resolve_session_turn(
        base_state,
        {"goal": goal, "meta": {"resend": True}},
        goal,
        incoming={"goal": goal, "meta": {"resend": True}},
    )
    assert decision.intent == "isolate_qa"
    assert decision.source == "user_resend"


def test_resend_classified_as_new_task_not_resume(base_state):
    goal = "写一份电影剧本，谍战剧情，要包括细节，民国背景"
    mission = {"kind": "writing", "objective": goal}
    state = merge_state(
        base_state,
        mission=mission,
        session_turn=3,
        input_payload={"mission": mission},
    )
    result = classify_user_event(
        state,
        payload={
            "goal": goal,
            "meta": {"resend": True},
            "turn_policy_decision": {"intent": "resume_mission"},
        },
    )
    assert result.event_type == "new_task"


def test_turn_policy_steer_correction_is_supersede(base_state):
    mission = {"kind": "writing", "objective": "写暗战同人", "total_target_chars": 400000}
    state = merge_state(base_state, mission=mission, session_turn=2)
    goal = "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动"
    decision = resolve_session_turn(
        state,
        {"goal": goal},
        goal,
        incoming={"goal": goal},
    )
    assert decision.intent == "supersede_active_mission"


def test_turn_policy_steer_correction_with_inherited_mission_payload(base_state):
    """Merged input_payload carries mission block — must not classify as resume."""
    from app.services.control_payload import merge_stripped_message_payload

    mission = {"kind": "writing", "objective": "写暗战同人", "total_target_chars": 400000}
    goal = "我认为，该电影不应该出现架空人物，即人物主角以黑客帝国3里出现过的人物为准"
    state = merge_state(
        base_state,
        mission=mission,
        session_turn=2,
        status="MISSION_RUNNING",
        input_payload={"goal": "写暗战同人", "mission": mission},
    )
    payload = merge_stripped_message_payload(state.get("input_payload"), text=goal)
    decision = resolve_session_turn(state, payload, goal, incoming=payload)
    assert decision.intent == "supersede_active_mission"
    assert decision.source == "steer_correction"


def test_inbound_steer_correction_classifies_redirect_not_resume(base_state):
    from app.services.session_controller import _resolve_inbound_dispatch
    from app.services.control_payload import merge_stripped_message_payload

    mission = {"kind": "writing", "objective": "写暗战同人", "total_target_chars": 400000}
    goal = "我认为，该电影不应该出现架空人物，即人物主角以黑客帝国3里出现过的人物为准"
    state = merge_state(
        base_state,
        mission=mission,
        session_turn=2,
        status="MISSION_RUNNING",
        fsm_state="RUNNING",
        input_payload={"goal": "写暗战同人", "mission": mission},
    )
    payload = merge_stripped_message_payload(state.get("input_payload"), text=goal)
    _inbound, event = _resolve_inbound_dispatch(state, payload)
    assert event.event_type == "redirect"
    assert event.event_type != "resume"


def test_steer_requires_planning_after_session_steer_apply(base_state):
    from app.services.mission_steer import apply_steer_message

    mission = {"kind": "writing", "objective": "写暗战同人"}
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.REJECTED.value,
        input_payload={"goal": "写暗战同人", "mission": mission},
    )
    updated = apply_steer_message(
        state,
        "使用原电影人物，不要架空人物",
        persist=False,
    )
    payload = updated.get("input_payload") or {}
    assert payload.get("latest_steer_message")
    assert steer_requires_planning(payload)
    assert planning_steer_replan_active(payload, updated)


def test_prepare_mission_skips_duplicate_steer_apply(base_state):
    from app.services.graph_runner import _prepare_mission_for_turn

    mission = {"kind": "writing", "objective": "写暗战同人"}
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "goal": "使用原电影人物，不要架空人物",
            "latest_steer_message": "使用原电影人物，不要架空人物",
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "mission": mission,
        },
    )
    prepared = _prepare_mission_for_turn(state, state["input_payload"], created=False)
    payload = prepared.get("input_payload") or {}
    assert payload.get("latest_steer_message") == "使用原电影人物，不要架空人物"
    assert steer_requires_planning(payload)


def test_planning_steer_replan_active_with_latest_steer_only(base_state):
    mission = {"kind": "writing"}
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "goal": "写暗战同人\n\n[steer] 使用原电影人物",
            "latest_steer_message": "使用原电影人物，不要架空人物",
            "steer_applied_at": "2026-06-05T00:00:00Z",
            "steer_planning_done": False,
            "mission": mission,
        },
    )
    assert planning_steer_replan_active(state["input_payload"], state)
