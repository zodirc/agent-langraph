"""Turn kind resolution and pipeline phase routing."""

from app.runtime.state import merge_state
from app.services.session_turn import prepare_session_turn
from app.services.mission.batch_unit_work_plan import build_batch_unit_work_plan_patch
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_planning_gate, complete_steer_planning
from app.services.turn_kind import (
    agenda_has_executor_pending,
    pipeline_phase_after_planning,
    plan_steps_for_display,
    resolve_turn_kind,
    should_use_reasoning_terminal,
)
from app.services.mission_invariants import mission_output_must_pause


def _writing_mission_state(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    patch = build_batch_unit_work_plan_patch(
        merge_state(
            base_state,
            mission=mission,
            manuscript={"last_chapter_index": 2, "body_bytes": 3000},
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
                },
            },
        )
    )
    items = list(patch["prepend"])
    for row in items:
        row.setdefault("status", "pending")
    return merge_state(
        base_state,
        mission=mission,
        manuscript={"last_chapter_index": 2, "body_bytes": 3000},
        progress={"work_plan": {"items": items, "current_id": items[0]["id"]}},
        input_payload={
            "turn_contract": {
                "primary_op": "batch_unit_quality",
                "forbid": ["append_body"],
            },
            "writing_intent": {"enabled": False, "action": "batch_unit_quality"},
        },
    )


def test_steer_gate_stamps_replan_and_applied_at():
    payload = apply_steer_planning_gate({})
    assert payload["turn_kind"] == "steer_replan"
    assert payload.get("steer_applied_at")


def test_complete_steer_stamps_execute():
    payload = complete_steer_planning(apply_steer_planning_gate({"steer_applied_at": "t"}))
    assert payload["turn_kind"] == "steer_execute"
    assert payload["steer_planning_done"] is True


def test_resolve_turn_kind_after_steer_planning(base_state):
    payload = complete_steer_planning(
        apply_steer_planning_gate({"steer_applied_at": "t", "goal": "review"})
    )
    state = merge_state(base_state, input_payload=payload, mission={"kind": "writing"})
    assert resolve_turn_kind(state) == "steer_execute"


def test_pipeline_execute_after_batch_planning(base_state):
    state = _writing_mission_state(base_state)
    state = merge_state(
        state,
        input_payload=complete_steer_planning(
            dict(state.get("input_payload") or {}, steer_applied_at="t")
        ),
    )
    assert agenda_has_executor_pending(state)
    assert pipeline_phase_after_planning(state) == "execute"
    assert not should_use_reasoning_terminal(state)


def test_plan_display_prefers_contract_and_agenda(base_state):
    state = _writing_mission_state(base_state)
    lines = plan_steps_for_display(state)
    assert any("batch_unit_quality" in line for line in lines)
    assert any(line.startswith("agenda:") for line in lines)


def test_mission_output_must_pause_with_pending_agenda(base_state):
    state = _writing_mission_state(base_state)
    assert mission_output_must_pause(state)


def test_pause_contract_requires_side_effects_when_agenda_pending(base_state):
    from app.services.turn_contract import contract_requires_side_effects

    state = _writing_mission_state(base_state)
    payload = {
        **(state.get("input_payload") or {}),
        "turn_contract": {"primary_op": "pause", "forbid": ["append_body"], "ops": []},
        "writing_intent": {"enabled": False, "action": "pause"},
    }
    assert not contract_requires_side_effects(payload)
    assert contract_requires_side_effects(payload, state=merge_state(state, input_payload=payload))


def test_session_turn_material_steer_uses_apply_steer_message(
    test_settings, isolated_stores, monkeypatch, base_state
):
    import app.services.session_turn as st_mod

    monkeypatch.setattr(st_mod, "settings", test_settings)
    from app.services.state_store import StateStore
    from app.runtime.state import TaskStatus

    store = StateStore(test_settings.SQLITE_PATH)

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state1, _ = prepare_session_turn(
        session_id="sess-steer",
        user_id="u1",
        task_type="qa",
        payload={
            "goal": "写长篇",
            "execution_mode": "mission",
            "mission": mission,
        },
    )
    state1 = merge_state(
        state1,
        mission=mission,
        execution_mode="mission",
        status=TaskStatus.MISSION_PAUSED.value,
    )
    store.save(state1)

    state2, _ = prepare_session_turn(
        session_id="sess-steer",
        user_id="u1",
        task_type="qa",
        payload={"goal": "逐章审阅并优化已写章节"},
    )
    pl = state2.get("input_payload") or {}
    assert pl.get("turn_kind") == "steer_replan"
    assert pl.get("steer_applied_at")
    assert pl.get("require_planning_after_steer") is True
    assert pl.get("writing_intent", {}).get("enabled") is False
