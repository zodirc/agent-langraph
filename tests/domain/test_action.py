"""Golden tests for the unified Action contract (unified-core refactor).

These assert the two properties the old translation chain failed to guarantee:
1. Zero translation gap — an Action renders directly to the backend tool call.
2. Edit honesty — a zero-replacement edit is NOT treated as success.
"""

from __future__ import annotations

from app.domain.action import (
    Action,
    answer,
    edit_artifact,
    is_edit_applied,
    read_artifact,
    write_artifact,
)


def test_edit_action_maps_directly_to_tool_params():
    a = edit_artifact("outline.txt", old_text="旧句", new_text="新句", occurrence_index=2)
    call = a.as_tool_call("task-1")
    assert call == {
        "name": "edit_text_artifact",
        "params": {
            "filename": "outline.txt",
            "old_text": "旧句",
            "new_text": "新句",
            "occurrence_index": 2,
            "task_id": "task-1",
        },
    }


def test_edit_action_batch_and_line_range():
    a = edit_artifact("body.txt", start_line=10, end_line=20, new_text="收紧的段落")
    call = a.as_tool_call("t")
    assert call["params"]["start_line"] == 10
    assert call["params"]["end_line"] == 20


def test_read_action_supports_scoped_read():
    a = read_artifact("body.txt", start_line=5, end_line=8)
    call = a.as_tool_call("t")
    assert call["name"] == "read_text_artifact"
    assert call["params"]["start_line"] == 5


def test_write_action_completes_turn():
    a = write_artifact("essay.txt", "全文……")
    assert a.completes_turn is True
    assert a.as_tool_call("t")["name"] == "write_text_artifact"


def test_answer_action_is_node_handled():
    a = answer("final response")
    assert a.is_terminal is True
    assert a.as_tool_call("t") is None  # no backing tool; node produces it


def test_edit_honesty_zero_replacements_is_not_applied():
    assert is_edit_applied({"status": "ok", "replacements": 0}) is False
    assert is_edit_applied({"status": "ok", "replacements": 1}) is True
    assert is_edit_applied({"status": "error"}) is False
    assert is_edit_applied({}) is False


def test_unknown_action_type_rejected():
    import pytest

    with pytest.raises(ValueError):
        Action(type="frobnicate")  # type: ignore[arg-type]


def test_action_roundtrip_dict():
    a = edit_artifact("x.txt", old_text="a", new_text="b", rationale="why")
    restored = Action.from_dict(a.to_dict())
    assert restored.type == a.type
    assert restored.params == a.params
    assert restored.rationale == "why"
