"""Multi-artifact edit requests fall through to LLM planning with explicit filename."""

from __future__ import annotations

from unittest.mock import patch

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
    (target / "散文.txt").write_text("散文正文", encoding="utf-8")
    (target / "大纲.txt").write_text("大纲内容", encoding="utf-8")


def test_multi_artifact_prose_hint_uses_thin_path(monkeypatch, tmp_path, isolated_stores):
    from unittest.mock import patch

    task_id = "plan-artifact-multi"
    _seed_two_artifacts(monkeypatch, tmp_path, task_id)
    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "把散文那篇润色一下", "risk_level": "LOW"},
    )
    with patch("app.nodes.planning_node.invoke_structured") as mock_invoke:
        mock_invoke.side_effect = AssertionError("disambiguated thin path must skip LLM")
        out = planning_node(state)

    mock_invoke.assert_not_called()
    actions = out.get("planned_actions") or []
    assert actions[0]["params"]["filename"] == "散文.txt"
    assert [a["type"] for a in actions] == ["read_artifact", "write_artifact"]


@patch("app.nodes.planning_node.invoke_structured")
def test_ambiguous_multi_artifact_llm_gets_write_patch(mock_invoke, monkeypatch, tmp_path, isolated_stores):
    task_id = "plan-artifact-ambiguous"
    _seed_two_artifacts(monkeypatch, tmp_path, task_id)
    mock_invoke.return_value = {
        "plan": ["read file", "polish", "save file"],
        "actions": [{"type": "read_artifact", "params": {"filename": "散文.txt"}}],
        "risk_level": "LOW",
        "skip_retrieval": True,
    }
    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "润色一下", "risk_level": "LOW"},
    )
    with patch("app.nodes.planning_node.trace_enabled", return_value=False):
        out = planning_node(state)

    mock_invoke.assert_called_once()
    assert out.get("status") == TaskStatus.PLANNED.value
    actions = out.get("planned_actions") or []
    assert [a["type"] for a in actions] == ["read_artifact", "write_artifact"]
    assert "write_text_artifact" in (out.get("selected_tools") or [])
