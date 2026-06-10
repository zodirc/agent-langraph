from app.domain.action import Action
from app.services.artifact_edit_intent import (
    artifact_edit_needs_write_after_reads,
    detect_artifact_edit_intent,
    ensure_artifact_edit_write_action,
    is_artifact_edit_goal,
    plan_implies_artifact_save,
    resolve_artifact_edit_filename,
)
from app.services.interaction_goal import goal_is_conversational_qa
from app.services.pre_planning import artifact_edit_thin_actions


def test_is_artifact_edit_goal_polish():
    assert is_artifact_edit_goal("你重新试试这个润色") is True
    assert is_artifact_edit_goal("润色一下") is True
    assert is_artifact_edit_goal("其实我想要你优化的是故事那一篇") is True
    assert is_artifact_edit_goal("你好") is False


def test_goal_is_conversational_qa_excludes_edit_verbs():
    assert goal_is_conversational_qa("你重新试试这个润色") is False
    assert goal_is_conversational_qa("你好") is True


def test_resolve_filename_from_story_hint(monkeypatch, tmp_path):
    from tests.conftest import patch_task_artifact_dir

    task_id = "resolve-story"
    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True)
    (target / "北平的车辙_散文.txt").write_text("散文", encoding="utf-8")
    (target / "北平的车辙_故事.txt").write_text("故事", encoding="utf-8")

    assert (
        resolve_artifact_edit_filename(task_id, "其实我想要你优化的是故事那一篇")
        == "北平的车辙_故事.txt"
    )
    assert resolve_artifact_edit_filename(task_id, "润色散文") == "北平的车辙_散文.txt"


def test_ensure_write_action_appended():
    actions = [
        Action(type="read_artifact", params={"filename": "a.txt"}, source="llm"),
    ]
    plan = ["读取文件", "润色优化", "保存优化后的版本"]
    out, patched = ensure_artifact_edit_write_action(
        actions,
        plan,
        goal="优化故事",
        task_id="t1",
    )
    assert patched is True
    assert [a.type for a in out] == ["read_artifact", "write_artifact"]
    assert out[1].params["filename"] == "a.txt"


def test_artifact_edit_needs_write_after_reads():
    state = {
        "input_payload": {"goal": "优化故事那一篇"},
        "plan": ["读故事", "润色", "保存"],
    }
    tools = [
        {"tool": "read_text_artifact", "status": "ok"},
        {"tool": "read_text_artifact", "status": "ok"},
        {"tool": "read_text_artifact", "status": "ok"},
    ]
    assert artifact_edit_needs_write_after_reads(state, tools) is True
    tools.append({"tool": "write_text_artifact", "status": "ok"})
    assert artifact_edit_needs_write_after_reads(state, tools) is False


def test_plan_implies_save_from_steps():
    assert plan_implies_artifact_save("hello", ["读取", "保存优化后的故事"]) is True
    assert plan_implies_artifact_save("hello", ["explain topic"]) is False


def test_detect_artifact_edit_intent_requires_artifacts(monkeypatch, tmp_path):
    from tests.conftest import patch_task_artifact_dir

    task_id = "artifact-edit-detect"
    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True)
    (target / "essay.txt").write_text("正文", encoding="utf-8")

    state = {"task_id": task_id, "input_payload": {"goal": "润色一下"}}
    assert detect_artifact_edit_intent(state, "润色一下") is True
    assert detect_artifact_edit_intent(state, "你好") is False

    empty = tmp_path / "no-artifacts"
    empty.mkdir()
    state2 = {"task_id": "no-artifacts", "input_payload": {}}
    assert detect_artifact_edit_intent(state2, "润色一下") is False


def test_thin_actions_multi_file_story_goal(monkeypatch, tmp_path):
    from app.runtime.state import create_initial_state
    from tests.conftest import patch_task_artifact_dir

    task_id = "thin-multi-story"
    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True)
    (target / "北平的车辙_散文.txt").write_text("散文", encoding="utf-8")
    (target / "北平的车辙_故事.txt").write_text("故事", encoding="utf-8")

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "其实我想要你优化的是故事那一篇"},
    )
    actions = artifact_edit_thin_actions(state)
    assert [a.type for a in actions] == ["read_artifact", "write_artifact"]
    assert actions[0].params["filename"] == "北平的车辙_故事.txt"
    assert actions[1].params["filename"] == "北平的车辙_故事.txt"
