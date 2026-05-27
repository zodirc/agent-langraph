from app.services.artifact_content import _read_artifact_snippet, needs_generated_content
from app.services.artifact_tools import handle_write_text_artifact


def test_needs_generated_empty():
    assert needs_generated_content("", "续写") is True


def test_needs_generated_placeholder():
    assert needs_generated_content("（占位：请续写）", "续写") is True


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
