from pathlib import Path

from app.services.manuscript_service import (
    artifact_bytes_on_disk,
    manuscript_has_body,
    resolve_manuscript,
)
from app.services.mission_schema import resolve_writing_intent_for_step
from app.runtime.state import merge_state


def test_manuscript_has_body_uses_disk_when_state_bytes_zero(
    base_state, test_settings, monkeypatch
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = base_state["task_id"]
    art_dir = Path(test_settings.ARTIFACTS_PATH) / task_id
    art_dir.mkdir(parents=True, exist_ok=True)
    novel = art_dir / "novel.txt"
    novel.write_text("已有正文。" * 200, encoding="utf-8")

    ms = resolve_manuscript(task_id, {"body_path": "novel.txt", "body_bytes": 0})
    assert artifact_bytes_on_disk(task_id, "novel.txt") > 0
    assert manuscript_has_body(ms)


def test_resolve_writing_intent_append_when_body_on_disk_only(
    base_state, test_settings, monkeypatch
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = base_state["task_id"]
    art_dir = Path(test_settings.ARTIFACTS_PATH) / task_id
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "novel.txt").write_text("第一章内容。" * 100, encoding="utf-8")
    (art_dir / "outline.txt").write_text("大纲" * 50, encoding="utf-8")

    mission = {
        "kind": "writing",
        "step_policy": {
            "chars_per_step": 3000,
            "first_step": "outline",
            "then": "append_body",
        },
    }
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=2,
        manuscript={"body_path": "novel.txt", "body_bytes": 0, "outline_path": "outline.txt", "outline_bytes": 100},
    )
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "append_body"
