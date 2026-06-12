"""Tests for writing project contract (方案 A)."""

from app.domain.action import Action
from app.services.artifact_tools import handle_read_text_artifact, handle_write_text_artifact
from app.services.writing_playbook import apply_writing_playbook
from app.services.writing_project import (
    ensure_writing_project,
    load_project,
    writing_project_manifest_exists,
)


def test_kickoff_body_writes_chapter_not_outline(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "writing-project-kickoff"
    ensure_writing_project(task_id)
    outline_content = "第一章：觉醒\n\n主角登场。"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "大纲.md", "content": outline_content}
    )

    actions, plan, patched = apply_writing_playbook(
        [],
        operator="kickoff_body",
        goal="开始写正文",
        task_id=task_id,
    )
    assert patched is True
    assert actions[0].params["filename"] == "大纲.md"
    body_file = actions[1].params["filename"]
    assert body_file == "正文/第001章.md"
    assert "大纲" not in body_file

    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": body_file,
            "content": "第一章正文内容。" * 400,
            "writing_operator": "kickoff_body",
        }
    )

    outline_after = handle_read_text_artifact(
        {"task_id": task_id, "filename": "大纲.md"}
    )["content"]
    assert outline_after == outline_content
    assert writing_project_manifest_exists(task_id)
    project = load_project(task_id)
    assert project is not None
    assert project.next_chapter >= 2


def test_body_write_guard_rejects_outline_target(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "writing-guard"
    ensure_writing_project(task_id)
    result = handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": "大纲.md",
            "content": "误写正文",
            "writing_operator": "kickoff_body",
        }
    )
    assert result.get("status") == "ok"
    assert "正文/第001章.md" in str(result.get("filename") or result.get("path") or "")

