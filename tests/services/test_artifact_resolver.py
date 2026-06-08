import pytest

from app.runtime.state import create_initial_state, merge_state
from app.services.artifact_resolver import (
    ArtifactResolutionError,
    ArtifactRole,
    build_artifact_manifest,
    outline_exists,
    resolve_artifact_target,
)
from app.services.manuscript_service import resolve_manuscript


@pytest.fixture
def art_dir(tmp_path, monkeypatch):
    from tests.conftest import patch_task_artifact_dir

    patch_task_artifact_dir(monkeypatch, tmp_path)
    return tmp_path


def test_resolve_edit_plot_targets_outline_not_planned_body(art_dir):
    task_id = "art-steer"
    art = art_dir / task_id
    art.mkdir(parents=True)
    outline = "锚点纪元_大纲.txt"
    (art / outline).write_text("# 大纲\n第一章\n", encoding="utf-8")

    state = merge_state(
        create_initial_state(task_id=task_id),
        mission={
            "kind": "writing",
            "step_policy": {
                "body_artifact": "锚点纪元.txt",
                "outline_artifact": "锚点纪元_大纲.txt",
            },
        },
        manuscript={"outline_path": outline, "outline_bytes": 100},
        input_payload={
            "turn_contract": {"primary_op": "edit_plot"},
            "writing_command": {
                "command_id": "c1",
                "action": "edit_plot",
                "target_kind": "outline",
                "target_filename": outline,
                "edit_spec": {"filename": outline},
            },
        },
    )
    target = resolve_artifact_target(state, action="edit_plot", target_hint="outline")
    assert target.filename == outline
    assert target.role == ArtifactRole.OUTLINE


def test_resolve_body_read_fails_when_only_outline_on_disk(art_dir):
    task_id = "art-body-miss"
    art = art_dir / task_id
    art.mkdir(parents=True)
    (art / "锚点纪元_大纲.txt").write_text("outline", encoding="utf-8")

    state = merge_state(
        create_initial_state(task_id=task_id),
        mission={
            "kind": "writing",
            "step_policy": {"body_artifact": "锚点纪元.txt", "outline_artifact": "锚点纪元_大纲.txt"},
        },
        manuscript={"outline_path": "锚点纪元_大纲.txt", "outline_bytes": 10},
    )
    with pytest.raises(ArtifactResolutionError) as exc:
        resolve_artifact_target(state, action="read", target_hint="body", require_exists=True)
    assert exc.value.code == "not_found"
    assert exc.value.manifest


def test_manifest_marks_planned_body(art_dir):
    task_id = "art-manifest"
    art = art_dir / task_id
    art.mkdir(parents=True)
    (art / "锚点纪元_大纲.txt").write_text("x" * 200, encoding="utf-8")
    payload = {
        "mission": {
            "kind": "writing",
            "step_policy": {"body_artifact": "锚点纪元.txt", "outline_artifact": "锚点纪元_大纲.txt"},
        }
    }
    ms = resolve_manuscript(task_id)
    manifest = build_artifact_manifest(task_id, manuscript=ms, payload=payload)
    planned = [e for e in manifest if e.filename == "锚点纪元.txt"]
    assert planned and planned[0].planned_only is True
    assert planned[0].exists is False


def test_outline_exists_true(art_dir):
    task_id = "art-exists"
    art = art_dir / task_id
    art.mkdir(parents=True)
    (art / "o.txt").write_text("outline content", encoding="utf-8")
    state = merge_state(
        create_initial_state(task_id=task_id),
        manuscript={"outline_path": "o.txt", "outline_bytes": 20},
    )
    assert outline_exists(state) is True
