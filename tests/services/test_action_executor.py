"""LLM-free golden tests for the unified Action executor (unified-core WP-1).

Covers the write → read → edit pipeline on a temp artifact dir, plus the edit
honesty contract: zero replacements (or a failed edit) is NOT applied.
"""

from __future__ import annotations

import pytest

from app.domain.action import answer, edit_artifact, read_artifact, run_tool, write_artifact
from app.runtime.state import create_initial_state, merge_state
from app.services.action_executor import execute_actions, has_planned_actions


@pytest.fixture()
def artifact_env(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr("app.services.artifact_tools.settings", test_settings)
    monkeypatch.setattr(test_settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    return tmp_path / "artifacts"


def _state_with_actions(actions, task_id="task-act"):
    state = create_initial_state(task_id=task_id, input_payload={"query": "test"})
    return merge_state(state, planned_actions=[a.to_dict() for a in actions])


def test_write_read_edit_pipeline(artifact_env):
    state = _state_with_actions(
        [
            write_artifact("draft.txt", "第一段。\n旧句子。\n第三段。"),
            read_artifact("draft.txt"),
            edit_artifact("draft.txt", old_text="旧句子。", new_text="新句子。"),
        ]
    )
    updated = execute_actions(state)

    results = updated["tool_results"]
    assert [r["tool"] for r in results] == [
        "write_text_artifact",
        "read_text_artifact",
        "edit_text_artifact",
    ]
    assert all(r["status"] == "ok" for r in results)
    assert "旧句子。" in results[1]["result"]["content"]
    assert results[2]["result"]["replacements"] == 1

    content = (artifact_env / "task-act" / "draft.txt").read_text(encoding="utf-8")
    assert "新句子。" in content
    assert "旧句子。" not in content

    facts = updated["turn_facts"]
    assert facts["edit_applied"] is True
    assert facts["edits_attempted"] == 1
    assert facts["edits_applied"] == 1


def test_edit_zero_replacements_is_not_applied(artifact_env):
    state = _state_with_actions(
        [
            write_artifact("draft.txt", "正文内容。"),
            # Batch edit whose entries are all skipped -> status ok, replacements 0.
            edit_artifact("draft.txt", edits=[{"old_text": "", "new_text": "x"}]),
        ],
        task_id="task-zero",
    )
    updated = execute_actions(state)

    edit_result = updated["tool_results"][-1]
    assert edit_result["status"] == "ok"
    assert edit_result["result"]["replacements"] == 0
    assert updated["turn_facts"]["edit_applied"] is False


def test_edit_missing_old_text_surfaces_error_not_silent_success(artifact_env):
    state = _state_with_actions(
        [
            write_artifact("draft.txt", "正文内容。"),
            edit_artifact("draft.txt", old_text="不存在的句子", new_text="替换"),
        ],
        task_id="task-miss",
    )
    updated = execute_actions(state)

    edit_result = updated["tool_results"][-1]
    assert edit_result["status"] == "error"
    assert edit_result["non_retryable"] is True
    assert updated["turn_facts"]["edit_applied"] is False
    # The file is untouched.
    assert (artifact_env / "task-miss" / "draft.txt").read_text(encoding="utf-8") == "正文内容。"


def test_edit_nonexistent_file_is_artifact_not_found(artifact_env):
    state = _state_with_actions(
        [edit_artifact("ghost.txt", old_text="a", new_text="b")],
        task_id="task-ghost",
    )
    updated = execute_actions(state)

    entry = updated["tool_results"][-1]
    assert entry["status"] == "error"
    assert entry["error_code"] == "artifact_not_found"
    assert updated["turn_facts"]["edit_applied"] is False


def test_run_tool_invokes_registry(artifact_env):
    state = _state_with_actions(
        [run_tool("echo", {"message": "hello"})],
        task_id="task-tool",
    )
    updated = execute_actions(state)

    entry = updated["tool_results"][-1]
    assert entry["tool"] == "echo"
    assert entry["status"] == "ok"
    assert entry["result"]["echo"] == "hello"


def test_run_tool_unknown_tool_is_non_retryable(artifact_env):
    state = _state_with_actions(
        [run_tool("does_not_exist", {})],
        task_id="task-unknown",
    )
    updated = execute_actions(state)

    entry = updated["tool_results"][-1]
    assert entry["status"] == "error"
    assert entry["error_code"] == "unknown_tool"
    assert entry["non_retryable"] is True


def test_answer_and_retrieve_are_deferred_to_nodes(artifact_env):
    state = _state_with_actions([answer("final")], task_id="task-answer")
    updated = execute_actions(state)

    assert not updated.get("tool_results")
    executed = updated["turn_facts"]["actions_executed"]
    assert executed == [{"type": "answer", "status": "deferred"}]


def test_has_planned_actions_ignores_node_handled_types():
    state = _state_with_actions([answer("final")], task_id="task-flags")
    assert has_planned_actions(state) is False
    state2 = _state_with_actions(
        [answer("final"), edit_artifact("a.txt", old_text="x", new_text="y")],
        task_id="task-flags2",
    )
    assert has_planned_actions(state2) is True


def test_invalid_action_dict_recorded_as_error(artifact_env):
    state = create_initial_state(task_id="task-bad", input_payload={})
    state = merge_state(state, planned_actions=[{"type": "frobnicate"}])
    updated = execute_actions(state)

    entry = updated["tool_results"][-1]
    assert entry["status"] == "error"
    assert entry["non_retryable"] is True


def test_no_planned_actions_is_noop():
    state = create_initial_state(task_id="task-noop", input_payload={})
    assert execute_actions(state) is state
