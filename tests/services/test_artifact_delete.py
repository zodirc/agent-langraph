from pathlib import Path

from app.services.artifact_tools import (
    artifacts_root,
    delete_task_artifact_dir,
    task_artifact_dir,
)


def test_delete_task_artifact_dir_removes_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_tools.settings.ARTIFACTS_PATH",
        str(tmp_path),
    )
    task_id = "sess-test-delete-001"
    art = task_artifact_dir(task_id)
    (art / "novel.txt").write_text("hello", encoding="utf-8")
    assert art.is_dir()

    assert delete_task_artifact_dir(task_id) is True
    assert not (artifacts_root() / task_id).exists()


def test_delete_task_artifact_dir_missing_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_tools.settings.ARTIFACTS_PATH",
        str(tmp_path),
    )
    assert delete_task_artifact_dir("no-such-session") is True
