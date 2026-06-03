"""Batch unit quality + contract validation + client display."""

from app.runtime.state import merge_state
from app.services.client_display import build_steer_task_client_display
from app.services.confirmation.gate_registry import GateContext, intent_gate_required
from app.services.mission.batch_unit_capability import enrich_planning_result_with_batch_unit
from app.services.mission.batch_unit_work_plan import build_batch_unit_work_plan_patch
from app.services.mission_execution import work_item_satisfied
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_planning_gate
from app.services.turn_contract import (
    contract_from_payload,
    is_turn_contract_fulfilled,
    validate_turn_contract_execution,
)


def test_batch_work_plan_includes_review_and_polish(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"last_chapter_index": 2, "body_bytes": 3000},
        input_payload=apply_steer_planning_gate({"steer_applied_at": "t"}),
        progress={
            "work_plan": {
                "items": [
                    {
                        "id": "wi-a",
                        "kind": "append_body",
                        "status": "pending",
                        "title": "append",
                    }
                ]
            }
        },
    )
    patch = build_batch_unit_work_plan_patch(state)
    kinds = [r["kind"] for r in patch["prepend"]]
    assert kinds.count("review_chapter") == 2
    assert kinds.count("polish_chapter") == 2
    assert patch["cancel_ids"] == ["wi-a"]


def test_enrich_overrides_pause_contract_with_batch_fallback(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"last_chapter_index": 3, "body_bytes": 9000},
        input_payload=apply_steer_planning_gate({"steer_applied_at": "t", "goal": "复查"}),
        progress={
            "work_plan": {
                "items": [
                    {"id": "wi-a", "kind": "append_body", "status": "pending", "title": "append"},
                ]
            }
        },
    )
    llm = {
        "plan": ["user requested stop"],
        "turn_contract": {"primary_op": "pause", "forbid": ["append_body"]},
        "writing_intent": {"enabled": False, "action": "pause"},
    }
    out = enrich_planning_result_with_batch_unit(llm, state)
    assert out["turn_contract"]["primary_op"] == "batch_unit_quality"
    assert out["writing_intent"]["action"] == "batch_unit_quality"


def test_validate_batch_contract_requires_review(base_state):
    payload = {
        "turn_contract": {
            "primary_op": "batch_unit_quality",
            "forbid": ["append_body"],
        },
        "writing_intent": {"enabled": False, "action": "batch_unit_quality"},
    }
    state = merge_state(base_state, input_payload=payload, tool_results=[])
    issues = validate_turn_contract_execution(state)
    assert any("contract_batch_unit_no_review" in i for i in issues)


def test_validate_batch_contract_fulfilled_after_review_audit(base_state):
    payload = {
        "turn_contract": {"primary_op": "batch_unit_quality", "forbid": ["append_body"]},
    }
    state = merge_state(
        base_state,
        input_payload=payload,
        observation={"executed_actions": ["writing:review_chapter"]},
        audit_log=[
            {
                "node": "writing",
                "action": "success",
                "detail": {"writing_phase": "review_chapter", "chapter_index": 1},
            }
        ],
    )
    assert is_turn_contract_fulfilled(state)


def test_plan_gate_large_work_plan_patch(base_state):
    result = {
        "work_plan_patch": {
            "prepend": [{"kind": "review_chapter", "title": f"c{i}"} for i in range(5)],
        }
    }
    payload = apply_steer_planning_gate({"steer_applied_at": "t"})
    assert intent_gate_required(
        GateContext(planning_result=result, payload=payload, mission_before={})
    )


def test_steer_queued_display_shows_goal_and_pause_append(base_state):
    state = merge_state(
        base_state,
        input_payload={},
        pending_user_message={"messages": [{"message": "请对已写章节逐章评分"}]},
    )
    display = build_steer_task_client_display(state, queued=True)
    text = "\n".join(display["system_lines"])
    assert "queued_goal" in text
    assert "逐章" in text or "评分" in text
    assert "续写已暂停" in text


def test_steer_applied_display_shows_batch_contract(base_state):
    payload = {
        "steer_applied_at": "t",
        "steer_planning_done": True,
        "turn_contract": {
            "primary_op": "batch_unit_quality",
            "forbid": ["append_body"],
        },
    }
    state = merge_state(base_state, input_payload=payload)
    display = build_steer_task_client_display(state, queued=False)
    text = "\n".join(display["system_lines"])
    assert "逐章" in text or "审阅" in text
    assert display["display"].get("turn_contract", {}).get("primary_op") == "batch_unit_quality"


def test_work_item_polish_satisfied_when_review_passed(base_state):
    from app.services.writing_phases import save_chapter_reviews

    task_id = base_state["task_id"]
    save_chapter_reviews(
        task_id,
        {
            "reviews": {
                "1": {
                    "pass": True,
                    "polish_recommended": False,
                }
            }
        },
    )
    mission = build_mission_dict(base_state, {"mission": {"kind": "writing"}}, kind="writing")
    state = merge_state(base_state, mission=mission)
    assert work_item_satisfied(
        "polish_chapter",
        state=state,
        mission=mission,
        params={"chapter_index": 1},
    )
