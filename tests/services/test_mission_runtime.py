from app.domain.packs.registry import resolve_mission_pack
from app.domain.packs.writing import WRITING_PACK
from app.runtime.state import create_initial_state, merge_state
from app.services.mission_service import init_mission_state, should_run_mission_runtime
from app.services.observation import build_observation
from app.services.progress_evaluator import evaluate_mission_control, evaluate_success_criteria


def test_should_run_mission_runtime():
    state = create_initial_state(input_payload={"mission": {"kind": "writing"}})
    assert should_run_mission_runtime(state, state["input_payload"])
    assert not should_run_mission_runtime(
        create_initial_state(), {"goal": "hello"}
    )


def test_init_writing_mission(base_state):
    payload = {
        **base_state["input_payload"],
        "goal": "编写同人小说",
        "mission": {
            "kind": "writing",
            "autonomous": True,
            "total_target_chars": 100000,
            "step_policy": {"chars_per_step": 3000},
        },
    }
    state = init_mission_state(base_state, payload)
    mission = state["mission"]
    assert mission["kind"] == "writing"
    assert mission["success_criteria"]["metric"] == "written_chars"
    assert mission["success_criteria"]["target"] == 100000
    assert mission["execution_mode"] == "autonomous"


def test_writing_pack_evaluate_success():
    mission = WRITING_PACK.parse_mission(
        create_initial_state(task_id="t1"),
        {"goal": "写一万字", "mission": {"target_chars": 10000}},
    )
    progress = {"metrics": {"written_chars": 12000}}
    ok, reason = WRITING_PACK.evaluate_success(mission, progress, {})
    assert ok
    assert "12000" in reason


def test_evaluate_mission_control_pause_on_max_steps(base_state):
    state = merge_state(
        base_state,
        mission={
            "kind": "single_turn",
            "budget": {"max_steps": 2, "max_failures": 3},
            "success_criteria": {"type": "steps_done", "target": 99},
        },
        progress={"steps_completed": 0, "consecutive_failures": 0},
        mission_step=2,
        observation={"has_failures": False},
    )
    result = evaluate_mission_control(state)
    assert result.done
    assert result.action == "pause"


def test_build_observation_includes_mission_step(base_state):
    state = merge_state(
        base_state,
        mission_step=3,
        mission={"id": "t", "kind": "writing"},
        tool_results=[{"tool": "calculator", "result": {"result": "3"}}],
    )
    obs = build_observation(state)
    assert obs["mission_step"] == 3
    assert obs["schema"] == "observation/v1"


def test_resolve_mission_pack_requires_explicit_kind():
    pack = resolve_mission_pack(payload={"goal": "续写万字小说"})
    assert pack.name == "single_turn"
    pack2 = resolve_mission_pack(
        payload={"mission": {"kind": "writing", "total_target_chars": 1000}}
    )
    assert pack2.name == "writing"
