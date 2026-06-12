"""Tests for writing project contract (single novel body file)."""

from app.services.artifact_tools import handle_read_text_artifact, handle_write_text_artifact
from app.services.writing_playbook import apply_writing_playbook
from app.services.writing_project import (
    CHAPTER_COMPLETION_RATIO,
    DEFAULT_BODY_FILE,
    DEFAULT_WORDS_PER_CHAPTER,
    ensure_writing_project,
    load_project,
    parse_target_chapter_from_goal,
    slice_current_chapter_text,
    writing_project_manifest_exists,
)


def test_kickoff_body_writes_single_novel_not_outline(isolated_stores, test_settings, monkeypatch):
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
    assert body_file == DEFAULT_BODY_FILE
    assert "大纲" not in body_file

    min_chars = int(DEFAULT_WORDS_PER_CHAPTER * CHAPTER_COMPLETION_RATIO)
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": body_file,
            "content": "第一章正文内容。" * (min_chars // 6 + 1) + "\n\n（第1章完）",
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
    assert project.body_file == DEFAULT_BODY_FILE
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
    assert DEFAULT_BODY_FILE in str(result.get("filename") or result.get("path") or "")


def test_slice_current_chapter_text_after_footer():
    full = "第一章内容。\n\n（第1章完）\n\n第二章开头。"
    assert slice_current_chapter_text(full) == "第二章开头。"
    assert slice_current_chapter_text("") == ""


def test_parse_target_chapter_from_goal():
    assert parse_target_chapter_from_goal("写到第20章") == 20
    assert parse_target_chapter_from_goal("续写1-20章") == 20
    assert parse_target_chapter_from_goal("连续写5章") == 5


def test_apply_goal_sets_target_and_words():
    project = ensure_writing_project("goal-parse", goal="写到第20章，每章4000字")
    assert project.target_chapter == 20
    assert project.words_per_chapter >= 4000


def test_post_chapter_short_chapter_stays_incomplete(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "short-chapter"
    project = ensure_writing_project(task_id)
    body = project.body_file
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": body,
            "content": "很短的一章。",
            "writing_operator": "append",
        }
    )
    updated = load_project(task_id)
    assert updated is not None
    assert updated.current_chapter_incomplete is True
    assert updated.next_chapter == 1


def test_merge_body_write_preserves_completed_chapters():
    from app.services.writing_project import merge_body_write_content

    full = "第一章正文。（第1章完）\n\n第二章开头"
    merged = merge_body_write_content(full, "第二章续写。（第2章完）")
    assert "第一章正文" in merged
    assert "（第1章完）" in merged
    assert "第二章续写" in merged


def test_extract_outline_for_chapter(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    from app.services.writing_project import ensure_writing_project, extract_outline_for_chapter

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "outline-slice"
    project = ensure_writing_project(task_id)
    outline = (
        "# 大纲\n\n## 第一章\n\n主角登场。\n\n## 第二章\n\n反派露面。\n\n## 第三章\n\n高潮。"
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": project.outline, "content": outline}
    )
    assert "反派露面" in extract_outline_for_chapter(task_id, 2)
    assert "主角登场" not in extract_outline_for_chapter(task_id, 2)


def test_body_draft_tool_name_switches_to_append_after_chapter_one(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    from app.services.writing_project import (
        body_draft_tool_name,
        ensure_writing_project,
        load_project,
        save_project,
    )

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "draft-tool-name"
    project = ensure_writing_project(task_id)
    body = project.body_file
    assert body_draft_tool_name(task_id, body) == "write_text_artifact"
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": body,
            "content": "第一章完。（第1章完）",
            "writing_operator": "kickoff_body",
        }
    )
    project = load_project(task_id)
    assert project is not None
    project.next_chapter = 2
    project.current_chapter_incomplete = False
    save_project(task_id, project)
    assert body_draft_tool_name(task_id, body) == "append_text_artifact"
