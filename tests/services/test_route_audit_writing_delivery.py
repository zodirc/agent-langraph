from app.runtime.state import create_initial_state, merge_state
from app.services.route_audit.audit import audit_planned_route


def test_writing_delivery_flags_reasoning_only_plan():
    state = merge_state(
        create_initial_state(
            task_id="ra-deliver",
            input_payload={
                "writing_intent": {"enabled": True},
                "interaction_goal": "delivery",
                "writing_operator": "kickoff_body",
                "goal": "开始写正文",
            },
        ),
        planned_actions=[{"type": "answer", "params": {}}],
        selected_tools=[],
    )
    audit = audit_planned_route(state)
    assert audit["aligned"] is False
    assert any("writing_delivery_requires_tools" in i for i in audit.get("issues") or [])
