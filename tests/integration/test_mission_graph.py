"""Mission graph: single_turn completes in one loop; writing continues until metric."""

from datetime import datetime, timedelta, timezone

from app.runtime.state import create_initial_state, merge_state
from app.services.progress_evaluator import evaluate_mission_control
from app.services.mission_service import init_mission_state
from app.runtime.mission_graph import run_mission_graph


def test_mission_graph_single_turn(base_state, isolated_stores):
    payload = {
        **base_state["input_payload"],
        "goal": "123+456是多少",
        "tool_params": {"calculator": {"expression": "123+456"}},
        "mission": {"kind": "single_turn"},
        "skip_retrieval": True,
    }
    state = init_mission_state(
        merge_state(base_state, input_payload=payload, execution_mode="mission"),
        payload,
    )
    final = run_mission_graph(state)
    assert final.get("mission")
    assert final.get("progress", {}).get("phase") in ("completed", "executing")
    assert final.get("final_answer")
    assert final["status"] in ("COMPLETED", "WAITING_REVIEW")


def test_mission_graph_writing_one_step(base_state, isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art
    from app.domain.packs import writing as writing_mod

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    monkeypatch.setattr(
        writing_mod.WRITING_PACK,
        "suggest_step_decision",
        lambda *a, **k: {
            "action": "continue",
            "next_executor": "subgraph:writing",
            "params": {},
            "rationale": "test",
        },
    )

    payload = {
        **base_state["input_payload"],
        "goal": "写一段科幻开头",
        "mission": {
            "kind": "writing",
            "total_target_chars": 999999,
            "max_steps": 1,
            "autonomous": True,
            "orchestration": {"enabled": False},
            "step_policy": {"chars_per_step": 3000, "first_step": "write_body"},
        },
        "writing_intent": {
            "enabled": True,
            "action": "write_body",
            "target_chars": 200,
            "min_chars": 50,
        },
    }
    state = init_mission_state(
        merge_state(base_state, input_payload=payload, execution_mode="mission"),
        payload,
    )
    from unittest.mock import patch

    fake = "。" * 300
    with patch(
        "app.nodes.writing_node._generate_validated_content",
        return_value=fake,
    ):
        final = run_mission_graph(state)

    ms = final.get("manuscript") or {}
    assert ms.get("body_bytes", 0) > 0 or any(
        r.get("tool") == "write_text_artifact" for r in (final.get("tool_results") or [])
    )
    assert final.get("mission_control", {}).get("done") is True
    assert final["mission_control"].get("action") == "pause"


def test_mission_wall_clock_pause(base_state):
    started = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    state = merge_state(
        base_state,
        mission={"budget": {"max_wall_sec": 30, "max_steps": 30, "max_failures": 3}},
        progress={
            "started_at": started,
            "steps_completed": 1,
            "consecutive_failures": 0,
            "phase": "executing",
        },
        observation={"has_failures": False},
        mission_step=2,
    )
    result = evaluate_mission_control(state)
    assert result.done is True
    assert result.action == "pause"
    assert "wall_clock" in result.reason
