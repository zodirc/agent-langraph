from app.runtime.state import create_initial_state, merge_state
from app.services.observation import build_observation
from app.services.mission_schema import build_mission_dict


def test_observation_refreshes_writing_metrics(monkeypatch, tmp_path):
    task_id = "t-obs-metrics"
    art_dir = tmp_path / task_id
    art_dir.mkdir()
    body = art_dir / "novel.txt"
    body.write_text("第一章\n\n" + ("正文内容。" * 200), encoding="utf-8")

    state = create_initial_state(task_id=task_id, user_id="u", task_type="writing")
    mission = build_mission_dict(
        state,
        {"mission": {"kind": "writing", "total_target_chars": 100000}},
        kind="writing",
    )
    state = merge_state(
        state,
        mission=mission,
        manuscript={"body_path": "novel.txt", "body_bytes": body.stat().st_size},
        progress={"metrics": {"written_chars": 0, "body_bytes": 0}},
    )

    monkeypatch.setattr(
        "app.services.manuscript_context.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    monkeypatch.setattr(
        "app.services.artifact_tools.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )

    obs = build_observation(state, mission=mission)
    metrics = obs["progress_metrics"]
    assert metrics["written_chars"] > 0
    assert metrics["written_chars"] != 0 or metrics["body_bytes"] > 0
