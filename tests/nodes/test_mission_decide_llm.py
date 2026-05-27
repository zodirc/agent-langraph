from app.nodes.mission_decide_node import mission_decide_node
from app.runtime.state import merge_state


def test_mission_decide_rules_when_llm_disabled(base_state, monkeypatch):
    import app.nodes.mission_decide_node as mod

    monkeypatch.setattr(mod.settings, "MISSION_LLM_DECIDE", False)
    state = merge_state(
        base_state,
        mission={
            "kind": "single_turn",
            "budget": {"max_steps": 5, "max_wall_sec": 3600, "max_failures": 3},
            "success_criteria": {"type": "steps_done", "target": 99},
        },
        progress={"steps_completed": 0, "consecutive_failures": 0},
        observation={"has_failures": False},
        mission_step=0,
    )
    result = mission_decide_node(state)
    assert result["step_decision"]["action"] == "continue"
    assert result["audit_log"][-1]["detail"]["source"] == "rules"


def test_mission_decide_llm_when_enabled(base_state, monkeypatch):
    import app.nodes.mission_decide_node as mod

    monkeypatch.setattr(mod.settings, "MISSION_LLM_DECIDE", True)
    state = merge_state(
        base_state,
        mission={
            "kind": "single_turn",
            "budget": {"max_steps": 5, "max_wall_sec": 3600, "max_failures": 3},
            "success_criteria": {"type": "steps_done", "target": 99},
        },
        progress={"steps_completed": 0, "consecutive_failures": 0},
        observation={"has_failures": False},
        mission_step=0,
    )
    result = mission_decide_node(state)
    assert result["step_decision"]["action"] in ("continue", "finish", "pause", "escalate", "retry")
    assert result["audit_log"][-1]["detail"]["source"] in ("llm", "rules", "eval_override")
