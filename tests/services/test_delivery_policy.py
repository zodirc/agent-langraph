from app.runtime.state import merge_state
from app.services.delivery_policy import (
    rewrite_manuscript_filenames_for_code,
    should_strip_writing_tools,
)
from app.services.route_audit.apply import apply_route_corrections
from app.services.route_audit.audit import audit_planned_route


def test_should_not_strip_tools_for_code_tools_only():
    assert (
        should_strip_writing_tools("writing_tools_only", inferred_kind="code") is False
    )
    assert (
        should_strip_writing_tools("writing_manuscript", inferred_kind="code") is True
    )


def test_rewrite_novel_filename_to_code():
    state = {
        "task_id": "task-abc-123",
        "input_payload": {
            "tool_params": {"write_text_artifact": {"filename": "novel.txt"}},
        },
    }
    payload, changed = rewrite_manuscript_filenames_for_code(state, extension=".cpp")
    assert changed is True
    name = payload["tool_params"]["write_text_artifact"]["filename"]
    assert name.endswith(".cpp")
    assert name != "novel.txt"


def test_apply_keeps_write_tool_for_code_tools_only():
    state = merge_state(
        {
            "task_id": "t-del",
            "session_id": "s-del",
            "input_payload": {
                "goal": "用 C++ 链表实现大数相加，写入文件",
                "tool_params": {
                    "write_text_artifact": {"filename": "novel.txt", "task_id": "t-del"},
                },
            },
            "selected_tools": ["write_text_artifact"],
            "plan": ["write source"],
        },
    )
    audit = audit_planned_route(state)
    assert audit["planned_route"] == "writing_tools_only"
    if not audit.get("aligned"):
        updated = apply_route_corrections(state, audit)
        assert "write_text_artifact" in (updated.get("selected_tools") or [])
        wt = (updated.get("input_payload") or {})["tool_params"]["write_text_artifact"]
        assert str(wt["filename"]).endswith(".cpp")
