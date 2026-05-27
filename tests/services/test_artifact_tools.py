import pytest

from app.services.artifact_tools import (
    extract_math_expression,
    handle_append_text_artifact,
    handle_calculator,
    handle_edit_text_artifact,
    handle_get_runtime_info,
    handle_write_text_artifact,
    list_task_artifacts,
)


def test_get_runtime_info_reports_model(test_settings, monkeypatch):
    import app.services.artifact_tools as artifact_tools_mod

    monkeypatch.setattr(artifact_tools_mod, "settings", test_settings)
    result = handle_get_runtime_info({"task_id": "t1"})
    assert result["model_name"] == test_settings.MODEL_NAME
    assert result["web_search"] is False
    assert "get_runtime_info" in result["available_tools"]


def test_write_and_append_artifact(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))

    write = handle_write_text_artifact(
        {"task_id": "task-a", "filename": "novel.md", "content": "# Chapter 1\n"}
    )
    assert write["status"] == "ok"
    assert (tmp_path / "artifacts" / "task-a" / "novel.md").exists()

    append = handle_append_text_artifact(
        {
            "task_id": "task-a",
            "filename": "novel.md",
            "content": "Once upon a time.\n",
        }
    )
    assert append["total_bytes"] > write["bytes"]
    listed = list_task_artifacts("task-a")
    assert listed[0]["filename"] == "novel.md"


def test_calculator_big_integer():
    expr = "1238102938102380123+123719823719237192739123"
    result = handle_calculator({"expression": expr})
    expected = str(1238102938102380123 + 123719823719237192739123)
    assert result["result"] == expected


def test_edit_text_artifact_replaces_once_and_writes_audit(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))

    class _Audit:
        def __init__(self):
            self.calls = []

        def append_events(self, task_id, events):
            self.calls.append((task_id, events))

    audit = _Audit()
    monkeypatch.setattr("app.services.artifact_tools.get_audit_store", lambda: audit)

    handle_write_text_artifact(
        {"task_id": "task-edit", "filename": "draft.txt", "content": "hello old world"}
    )
    result = handle_edit_text_artifact(
        {
            "task_id": "task-edit",
            "filename": "draft.txt",
            "old_text": "old",
            "new_text": "new",
            "user_role": "admin",
        }
    )
    assert result["status"] == "ok"
    assert result["replacements"] == 1
    content = (tmp_path / "artifacts" / "task-edit" / "draft.txt").read_text(encoding="utf-8")
    assert content == "hello new world"
    assert audit.calls
    task_id, events = audit.calls[0]
    assert task_id == "task-edit"
    assert events[0]["type"] == "artifact_edit"
    assert events[0]["tool"] == "edit_text_artifact"


def test_edit_text_artifact_rejects_multiple_matches_without_replace_all(
    tmp_path, test_settings, monkeypatch
):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "app.services.artifact_tools.get_audit_store",
        lambda: type("_Audit", (), {"append_events": lambda self, task_id, events: None})(),
    )
    handle_write_text_artifact(
        {"task_id": "task-edit-2", "filename": "draft.txt", "content": "x old y old z"}
    )
    with pytest.raises(ValueError, match="multiple locations"):
        handle_edit_text_artifact(
            {
                "task_id": "task-edit-2",
                "filename": "draft.txt",
                "old_text": "old",
                "new_text": "new",
                "user_role": "admin",
            }
        )


def test_edit_text_artifact_supports_occurrence_index_and_line_scope(
    tmp_path, test_settings, monkeypatch
):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "app.services.artifact_tools.get_audit_store",
        lambda: type("_Audit", (), {"append_events": lambda self, task_id, events: None})(),
    )
    handle_write_text_artifact(
        {
            "task_id": "task-edit-3",
            "filename": "draft.txt",
            "content": "line1\nold here\nline3\nold there\nline5",
        }
    )
    result = handle_edit_text_artifact(
        {
            "task_id": "task-edit-3",
            "filename": "draft.txt",
            "old_text": "old",
            "new_text": "new",
            "occurrence_index": 1,
            "start_line": 4,
            "end_line": 4,
            "user_role": "admin",
        }
    )
    assert result["status"] == "ok"
    assert result["replacements"] == 1
    content = (tmp_path / "artifacts" / "task-edit-3" / "draft.txt").read_text(encoding="utf-8")
    assert content == "line1\nold here\nline3\nnew there\nline5"
    assert result["selection"]["scope"]["start_line"] == 4
    assert result["selection"]["occurrence_index"] == 1


def test_edit_text_artifact_dry_run_returns_diff_without_writing(
    tmp_path, test_settings, monkeypatch
):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "app.services.artifact_tools.get_audit_store",
        lambda: type("_Audit", (), {"append_events": lambda self, task_id, events: None})(),
    )
    handle_write_text_artifact(
        {"task_id": "task-edit-4", "filename": "draft.txt", "content": "alpha beta"}
    )
    result = handle_edit_text_artifact(
        {
            "task_id": "task-edit-4",
            "filename": "draft.txt",
            "old_text": "beta",
            "new_text": "gamma",
            "dry_run": True,
            "user_role": "admin",
        }
    )
    assert result["dry_run"] is True
    assert "gamma" in result["diff_preview"]
    content = (tmp_path / "artifacts" / "task-edit-4" / "draft.txt").read_text(encoding="utf-8")
    assert content == "alpha beta"


def test_calculator_rejects_unsafe_expression():
    with pytest.raises(ValueError):
        handle_calculator({"expression": "__import__('os').system('ls')"})


def test_extract_math_expression():
    assert extract_math_expression("123+456是多少") == "123+456"
