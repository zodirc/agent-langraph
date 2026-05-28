"""mission_finalize uses checkpoint summary without artifact delta."""

from app.nodes.mission_finalize_node import mission_finalize_node
from app.runtime.state import merge_state
from app.services.mission_schema import build_mission_dict


def test_finalize_checkpoint_without_llm(base_state, monkeypatch):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    mission = {**mission, "orchestration": {"enabled": True, "stepwise": True}}
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1000, "body_bytes": 0},
        mission_control={"action": "pause", "pause_reason": "step_checkpoint"},
        observation={
            "artifact_delta": {"has_change": False},
            "manuscript": {"outline_path": "outline.txt", "outline_bytes": 1000, "body_bytes": 0},
        },
        progress={"metrics": {"written_chars": 0, "target_chars": 50000, "progress_pct": 0}},
    )

    def fail_reasoning(_s):
        raise AssertionError("reasoning_node should not run")

    monkeypatch.setattr("app.nodes.mission_finalize_node.reasoning_node", fail_reasoning)
    out = mission_finalize_node(state)
    rr = out.get("reasoning_result") or {}
    assert (rr.get("structured") or {}).get("source") == "mission_checkpoint"
    assert "resume" in str(rr.get("summary", "")).lower() or "继续" in str(rr.get("summary", ""))
