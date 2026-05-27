from app.runtime.state import merge_state
from app.services.mission_executor import execute_mission_step
from app.services.mission_schema import apply_mission_step_to_payload
from app.services.mission_service import prepare_state_for_mission_act
from app.services.writing_phases import apply_writing_phase_from_decision
from app.services.mission_steer import (
    apply_steer_message,
    complete_steer_planning,
    mission_must_run_planning,
    steer_requires_planning,
)
from app.nodes.planning_node import planning_node


def test_steer_message_sets_planning_gate(base_state):
    state = apply_steer_message(
        base_state,
        "按电视剧人物重写大纲",
        source="user",
    )
    payload = state.get("input_payload") or {}
    assert payload.get("require_planning_after_steer") is True
    assert payload.get("steer_planning_done") is False
    assert steer_requires_planning(payload) is True


def test_apply_mission_step_forces_planning_after_steer(base_state):
    mission = {
        "kind": "writing",
        "step_policy": {"chars_per_step": 3000, "first_step": "outline", "then": "append_body"},
    }
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "goal": "改大纲",
        },
    )
    payload = apply_mission_step_to_payload(state)
    assert payload.get("skip_planning_llm") is False


def test_mission_must_run_planning_when_gate_open(base_state):
    state = merge_state(
        base_state,
        input_payload={"require_planning_after_steer": True, "steer_planning_done": False},
    )
    assert mission_must_run_planning(state) is True
    payload = complete_steer_planning(state["input_payload"])
    state = merge_state(state, input_payload=payload)
    assert mission_must_run_planning(state) is False


def test_execute_mission_step_routes_to_planning_not_writing_subgraph(
    base_state, monkeypatch
):
    mission = {"kind": "writing", "step_policy": {"chars_per_step": 3000}}
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "require_planning_after_steer": True,
            "goal": "暂停续写，先改大纲",
        },
        step_decision={"action": "continue", "next_executor": "subgraph:writing"},
    )
    calls: list[str] = []

    def fake_pipeline(s):
        calls.append("pipeline")
        return merge_state(s, status="REASONED")

    def fake_writing(s):
        calls.append("writing")
        return s

    monkeypatch.setattr(
        "app.services.mission_executor.run_pipeline_request",
        fake_pipeline,
    )
    monkeypatch.setattr(
        "app.services.mission_executor.run_subgraph_writing",
        fake_writing,
    )
    execute_mission_step(state, state.get("step_decision") or {})
    assert calls == ["pipeline"]


def test_execute_mission_step_reads_current_work_item_without_name_error(
    base_state, monkeypatch
):
    mission = {
        "kind": "writing",
        "step_policy": {"chars_per_step": 3000, "first_step": "outline"},
        "orchestration": {"enabled": True, "stepwise": True},
    }
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "goal": "写大纲",
            "current_work_item": {
                "id": "wi-outline",
                "kind": "write_outline",
                "status": "active",
            },
            "writing_intent": {"enabled": True, "action": "write_outline"},
            "steer_planning_done": True,
        },
        step_decision={"action": "continue", "next_executor": "subgraph:writing"},
    )
    monkeypatch.setattr(
        "app.services.mission_executor.run_subgraph_writing",
        lambda s: merge_state(s, status="WRITTEN"),
    )

    out = execute_mission_step(state, state.get("step_decision") or {})
    assert out.get("status") == "WRITTEN"


def test_prepare_keeps_work_plan_write_outline_under_steer_gate(base_state):
    mission = {
        "kind": "writing",
        "step_policy": {
            "chars_per_step": 3000,
            "first_step": "outline",
            "then": "append_body",
        },
        "orchestration": {"enabled": True, "stepwise": True},
    }
    state = merge_state(
        base_state,
        mission=mission,
        progress={"work_plan": {"mode": "lazy", "items": [], "completed_ids": []}},
        input_payload={
            "require_planning_after_steer": True,
            "goal": "按电视剧人物写大纲",
        },
    )
    prepared = prepare_state_for_mission_act(state)
    payload = prepared.get("input_payload") or {}
    assert payload.get("skip_planning_llm") is False
    assert payload.get("current_work_item", {}).get("kind") == "write_outline"
    intent = payload.get("writing_intent") or {}
    assert intent.get("source") == "await_steer_planning"
    assert intent.get("enabled") is False


def test_writing_phase_does_not_override_steer_planning_gate(base_state):
    mission = {
        "kind": "writing",
        "step_policy": {"chars_per_step": 3000, "then": "append_body"},
    }
    state = merge_state(
        base_state,
        mission=mission,
        step_decision={
            "action": "continue",
            "params": {"writing_phase": "append_body", "chapter_index": 3},
        },
        input_payload={
            "require_planning_after_steer": True,
            "goal": "先改大纲",
        },
    )
    after_phase = apply_writing_phase_from_decision(state)
    assert (after_phase.get("input_payload") or {}).get("writing_intent") is None or not (
        (after_phase.get("input_payload") or {}).get("writing_intent") or {}
    ).get("enabled")

    prepared = prepare_state_for_mission_act(state)
    payload = prepared.get("input_payload") or {}
    assert payload.get("skip_planning_llm") is False
    assert mission_must_run_planning(prepared) is True


def test_mission_act_after_steer_runs_planning_then_clears_gate(base_state, monkeypatch):
    from app.nodes.mission_act_node import mission_act_node
    from app.runtime.state import TaskStatus

    mission = {
        "kind": "writing",
        "step_policy": {"chars_per_step": 3000, "first_step": "outline", "then": "append_body"},
        "orchestration": {"enabled": False},
    }
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_RUNNING.value,
        mission_step=0,
        step_decision={"action": "continue", "next_executor": "subgraph:writing"},
        input_payload={
            "goal": "按原剧人物重写大纲",
            "require_planning_after_steer": True,
            "conversation_history": [],
        },
    )

    def fake_pipeline(s):
        from app.services.mission_steer import complete_steer_planning

        payload = dict(s.get("input_payload") or {})
        payload = complete_steer_planning(payload)
        payload["writing_intent"] = {
            "enabled": True,
            "action": "write_outline",
            "source": "planning",
        }
        return merge_state(
            s,
            input_payload=payload,
            status=TaskStatus.PLANNED.value,
            plan=["rewrite outline"],
        )

    monkeypatch.setattr(
        "app.services.mission_executor.run_pipeline_request",
        fake_pipeline,
    )
    out = mission_act_node(state)
    p = out.get("input_payload") or {}
    assert p.get("steer_planning_done") is True
    assert not p.get("require_planning_after_steer")
    assert out.get("status") in (TaskStatus.MISSION_RUNNING.value, TaskStatus.PLANNED.value)


def test_planning_skip_blocked_while_steer_gate_open(base_state, monkeypatch):
    mission = {"kind": "writing", "step_policy": {"chars_per_step": 3000}}
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "require_planning_after_steer": True,
            "skip_planning_llm": True,
            "goal": "重写大纲",
        },
    )
    invoked: list[bool] = []

    def fake_invoke(*_a, **_k):
        invoked.append(True)
        return {
            "plan": ["rewrite outline"],
            "selected_tools": [],
            "risk_level": "LOW",
            "mission_intervention": {
                "action": "rewrite_outline",
                "force": True,
            },
        }

    monkeypatch.setattr("app.nodes.planning_node.invoke_structured", fake_invoke)
    monkeypatch.setattr("app.nodes.planning_node.stream_structured", fake_invoke)
    monkeypatch.setattr("app.nodes.planning_node.trace_enabled", lambda: False)
    out = planning_node(state)
    assert invoked
    assert out.get("input_payload", {}).get("steer_planning_done") is True
    assert not steer_requires_planning(out.get("input_payload") or {})
