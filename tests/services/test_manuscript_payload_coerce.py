from app.services.artifact_resolver import bind_planned_artifact_names
from app.services.manuscript_service import enrich_payload, resolve_manuscript


def test_bind_planned_names_sets_mission_policy():
    out = bind_planned_artifact_names(
        {
            "mission": {
                "kind": "writing",
                "step_policy": {"outline_artifact": "outline.txt"},
            },
            "writing_intent": {"enabled": True, "action": "write_outline"},
        },
        planning_result={"writing_intent": {"body_filename": "暗战.txt"}},
    )
    sp = out["mission"]["step_policy"]
    assert sp["body_artifact"] == "暗战.txt"
    assert sp["outline_artifact"] == "outline.txt"


def test_enrich_payload_includes_manifest(tmp_path, monkeypatch):
    task_id = "enrich-m"
    monkeypatch.setattr(
        "app.services.artifact_tools.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    art = tmp_path / task_id
    art.mkdir(parents=True)
    (art / "novel.txt").write_text("body", encoding="utf-8")
    ms = resolve_manuscript(task_id)
    out = enrich_payload({"goal": "续写"}, task_id, manuscript=ms)
    assert "artifact_manifest" in out
    assert "novel_filename" not in out
