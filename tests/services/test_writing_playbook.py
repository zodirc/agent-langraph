from app.domain.action import Action
from app.services.writing_playbook import apply_writing_playbook


def test_rewrite_playbook_injects_read_write(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "playbook-rewrite"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": "第一章\n\n旧内容。"}
    )
    actions, plan, patched = apply_writing_playbook(
        [Action(type="read_artifact", params={"filename": "novel.txt"})],
        operator="rewrite",
        goal="重写小说，内容太少",
        task_id=task_id,
    )
    assert patched is True
    assert [a.type for a in actions] == ["read_artifact", "write_artifact"]
    assert "重写" in " ".join(plan)


def test_playbook_skips_when_precise_edit_present():
    actions = [
        Action(type="read_artifact", params={"filename": "outline.txt"}),
        Action(
            type="edit_artifact",
            params={"filename": "outline.txt", "old_text": "地球", "new_text": "火星"},
        ),
    ]
    normalized, _, patched = apply_writing_playbook(
        actions,
        operator="polish",
        goal="把大纲里的地球改成火星",
        task_id="playbook-skip",
    )
    assert patched is False
    assert normalized == actions


def test_kickoff_body_playbook_reads_outline_writes_body(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "playbook-kickoff"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel_大纲.txt", "content": "第一章：觉醒"}
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": ""}
    )
    actions, plan, patched = apply_writing_playbook(
        [],
        operator="kickoff_body",
        goal="开始写正文",
        task_id=task_id,
    )
    assert patched is True
    assert [a.type for a in actions] == ["read_artifact", "write_artifact"]
    assert actions[0].params["filename"] == "novel_大纲.txt"
    assert actions[1].params["filename"] == "novel.txt"
    assert "撰写正文" in " ".join(plan)


def test_kickoff_body_with_existing_body_appends_without_outline_read(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    from app.services.writing_project import ensure_writing_project

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "playbook-kickoff-append"
    project = ensure_writing_project(task_id)
    from app.services.artifact_tools import handle_write_text_artifact

    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": project.body_file,
            "content": "第一章。（第1章完）",
            "writing_operator": "kickoff_body",
        }
    )
    actions, _, patched = apply_writing_playbook(
        [],
        operator="kickoff_body",
        goal="开始写正文，写1-10章",
        task_id=task_id,
        force_write=True,
    )
    assert patched is True
    assert len(actions) == 1
    assert actions[0].type == "run_tool"
    assert actions[0].params.get("name") == "append_text_artifact"
    assert not any(a.type == "read_artifact" for a in actions)


def test_append_playbook_uses_run_tool_append(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "playbook-append"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": "第一章\n\n已有内容。"}
    )
    actions, _, patched = apply_writing_playbook(
        [],
        operator="append",
        goal="续写下一章",
        task_id=task_id,
    )
    assert patched is True
    assert actions[0].type == "run_tool"
    assert actions[0].params.get("name") == "append_text_artifact"
