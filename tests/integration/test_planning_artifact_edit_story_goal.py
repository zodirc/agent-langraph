"""Multi-artifact session: goal names 故事 → thin path read+write without read-loop."""

from __future__ import annotations

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state


def _seed_two_artifacts(monkeypatch, tmp_path, task_id: str) -> None:
    from tests.conftest import patch_task_artifact_dir

    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "北平的车辙_散文.txt").write_text("散文正文", encoding="utf-8")
    (target / "北平的车辙_故事.txt").write_text("故事正文", encoding="utf-8")


def test_planning_story_goal_uses_thin_path(monkeypatch, tmp_path, isolated_stores):
    from unittest.mock import patch

    task_id = "plan-story-thin"
    _seed_two_artifacts(monkeypatch, tmp_path, task_id)

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "其实我想要你优化的是故事那一篇", "risk_level": "LOW"},
    )
    with patch("app.nodes.planning_node.invoke_structured") as mock_invoke:
        mock_invoke.side_effect = AssertionError("LLM planning must not run on disambiguated thin path")
        out = planning_node(state)

    mock_invoke.assert_not_called()
    assert out.get("status") == TaskStatus.PLANNED.value
    types = [a["type"] for a in (out.get("planned_actions") or [])]
    assert types == ["read_artifact", "write_artifact"]
    assert (out.get("planned_actions") or [])[0]["params"]["filename"] == "北平的车辙_故事.txt"
    assert "write_text_artifact" in (out.get("selected_tools") or [])
