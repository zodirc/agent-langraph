"""Planning transport resolves body write vs append for display/legacy tools."""

from app.domain.action import Action
from app.nodes.planning_node import _execution_transport_from_actions
from app.services.writing_playbook import _append_action, _read_action, _write_action
from app.services.writing_project import DEFAULT_BODY_FILE, ensure_writing_project


def test_execution_transport_uses_append_for_body_continuation(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "transport-append"
    ensure_writing_project(task_id)
    from app.services.artifact_tools import handle_write_text_artifact

    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": DEFAULT_BODY_FILE,
            "content": "第一章。（第1章完）",
            "writing_operator": "kickoff_body",
        }
    )
    from app.services.writing_project import load_project, save_project

    project = load_project(task_id)
    assert project is not None
    project.next_chapter = 2
    project.current_chapter_incomplete = False
    save_project(task_id, project)

    actions = [_read_action("大纲.md"), _write_action(DEFAULT_BODY_FILE)]
    tools, _, _ = _execution_transport_from_actions(actions, task_id=task_id)
    assert tools == ["read_text_artifact", "append_text_artifact"]

    append_only = [_append_action(DEFAULT_BODY_FILE)]
    tools2, _, _ = _execution_transport_from_actions(append_only, task_id=task_id)
    assert tools2 == ["append_text_artifact"]
