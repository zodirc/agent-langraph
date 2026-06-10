"""Planning fast path for single-artifact polish/revise requests."""

from __future__ import annotations

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state


def _seed_single_artifact(monkeypatch, tmp_path, task_id: str, filename: str, content: str) -> None:
    from tests.conftest import patch_task_artifact_dir

    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True, exist_ok=True)
    (target / filename).write_text(content, encoding="utf-8")


def test_planning_artifact_edit_thin_path(monkeypatch, tmp_path, isolated_stores):
    task_id = "plan-artifact-edit-1"
    filename = "北平的车辙_散文.txt"
    _seed_single_artifact(monkeypatch, tmp_path, task_id, filename, "北平的车辙，岁月如歌。")

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "润色一下", "risk_level": "LOW"},
    )
    out = planning_node(state)

    assert out.get("status") == TaskStatus.PLANNED.value
    actions = out.get("planned_actions") or []
    types = [a["type"] for a in actions]
    assert "read_artifact" in types
    assert "write_artifact" in types
    assert actions[0]["params"]["filename"] == filename

    tools = out.get("selected_tools") or []
    assert "read_text_artifact" in tools
    assert "write_text_artifact" in tools

    payload = out.get("input_payload") or {}
    assert payload.get("thin_execution_profile") == "artifact_edit"
    assert (payload.get("writing_intent") or {}).get("enabled") is True
    assert "write_text_artifact" in (out.get("selected_tools") or [])

    audit = payload.get("route_audit") or {}
    assert audit.get("writing_blocked") is not True


def test_planning_artifact_edit_short_polish_goal(monkeypatch, tmp_path, isolated_stores):
    task_id = "plan-artifact-edit-2"
    _seed_single_artifact(monkeypatch, tmp_path, task_id, "essay.txt", "草稿")

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "你重新试试这个润色", "risk_level": "LOW"},
    )
    out = planning_node(state)
    types = [a["type"] for a in (out.get("planned_actions") or [])]
    assert types == ["read_artifact", "write_artifact"]
