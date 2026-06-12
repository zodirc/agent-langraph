from unittest.mock import patch

from app.services.artifact_content import _read_artifact_snippet, generate_artifact_content, needs_generated_content
from app.services.artifact_tools import handle_write_text_artifact


def test_needs_generated_empty():
    assert needs_generated_content("", "续写") is True


def test_needs_generated_placeholder():
    assert needs_generated_content("（占位：请续写）", "续写") is True
    assert needs_generated_content(
        "第一章\n\n（正文内容由推理模块根据大纲生成）", "写第一章"
    ) is True


def test_needs_generated_short_goal_as_content():
    assert needs_generated_content("续写", "续写") is True


def test_accepts_real_content():
    text = "第一章\n\n" + "江湖夜雨十年灯。" * 50
    assert needs_generated_content(text, "续写") is False


def test_read_artifact_snippet_uses_task_artifact_dir(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "artifact-snippet-task"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "outline.txt", "content": "大纲片段"}
    )
    assert _read_artifact_snippet(task_id, "outline.txt") == "大纲片段"


@patch("app.services.artifact_content.artifact_stream_enabled", return_value=False)
@patch("app.services.artifact_content.trace_enabled", return_value=False)
@patch("app.services.artifact_content.invoke_artifact_draft")
def test_generate_artifact_content_injects_writing_guidelines(
    mock_invoke, _trace, _stream, isolated_stores
):
    mock_invoke.return_value = type("Draft", (), {"content": "第一章\n\n江湖夜雨。", "source": "tool"})()
    state = {
        "task_id": "writing-guidelines-task",
        "input_payload": {"goal": "续写下一章"},
        "retrieved_knowledge": [
            {
                "doc_id": "builtin-prose-voice-format",
                "content": "对话单独成行，段落之间空一行。",
                "metadata": {"domain": "writing"},
            }
        ],
    }
    generate_artifact_content(
        state=state,
        tool_name="append_text_artifact",
        filename="novel.txt",
        goal="续写下一章",
    )
    user_payload = mock_invoke.call_args.kwargs["user_payload"]
    excerpt = (user_payload.get("writing_context") or {}).get("writing_guidelines_excerpt") or ""
    assert "对话单独成行" in excerpt


@patch("app.services.artifact_content.artifact_stream_enabled", return_value=False)
@patch("app.services.artifact_content.trace_enabled", return_value=False)
@patch("app.services.artifact_content.invoke_artifact_draft")
def test_generate_artifact_content_injects_outline_for_chapter(
    mock_invoke, _trace, _stream, isolated_stores, test_settings, monkeypatch
):
    import app.services.artifact_tools as art

    from app.services.writing_project import ensure_writing_project

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    mock_invoke.return_value = type("Draft", (), {"content": "第二章正文。（第2章完）", "source": "tool"})()
    task_id = "outline-inject"
    project = ensure_writing_project(task_id)
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": project.outline,
            "content": "# 大纲\n\n## 第一章\n\n按大纲写反派登场。",
        }
    )
    state = {
        "task_id": task_id,
        "input_payload": {"goal": "写第一章", "target_mode": "manuscript_mode"},
    }
    generate_artifact_content(
        state=state,
        tool_name="append_text_artifact",
        filename=project.body_file,
        goal="写第一章",
    )
    ctx = (mock_invoke.call_args.kwargs["user_payload"] or {}).get("writing_context") or {}
    assert "反派登场" in str(ctx.get("outline_for_chapter") or "")
    assert ctx.get("chapter_index") == 1
