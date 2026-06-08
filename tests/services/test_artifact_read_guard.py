"""Repeated read_text_artifact on the same file is capped."""

from app.runtime.state import merge_state
from app.services.artifact_read_guard import (
    artifact_read_saturated,
    block_repeat_artifact_read,
    count_artifact_reads,
    filter_saturated_read_tools,
)


def _read_result(filename: str, content: str = "x") -> dict:
    return {
        "tool": "read_text_artifact",
        "status": "ok",
        "result": {"filename": filename, "content": content, "status": "ok"},
    }


def test_count_reads_same_basename(base_state, test_settings, monkeypatch):
    import app.services.artifact_read_guard as guard

    monkeypatch.setattr(guard.settings, "ARTIFACT_MAX_READS_SAME_FILE", 2)
    state = merge_state(
        base_state,
        tool_results=[
            _read_result("暗流涌动_大纲.txt"),
            _read_result("暗流涌动_大纲.txt"),
        ],
    )
    assert count_artifact_reads(state, "暗流涌动_大纲.txt") == 2
    assert artifact_read_saturated(state, "暗流涌动_大纲.txt")


def test_block_returns_cached_content(base_state, test_settings, monkeypatch):
    import app.services.artifact_read_guard as guard

    monkeypatch.setattr(guard.settings, "ARTIFACT_MAX_READS_SAME_FILE", 2)
    state = merge_state(
        base_state,
        tool_results=[
            _read_result("novel.txt", "cached body"),
            _read_result("novel.txt", "cached body"),
        ],
    )
    blocked = block_repeat_artifact_read(state, filename="novel.txt")
    assert blocked is not None
    assert blocked.get("status") == "cached"
    assert blocked.get("content") == "cached body"


def test_filter_drops_read_from_plan(base_state, test_settings, monkeypatch):
    import app.services.artifact_read_guard as guard

    monkeypatch.setattr(guard.settings, "ARTIFACT_MAX_READS_SAME_FILE", 1)
    state = merge_state(base_state, tool_results=[_read_result("a.txt")])
    tools = filter_saturated_read_tools(
        state,
        ["read_text_artifact", "calculator"],
        {"read_text_artifact": {"filename": "a.txt"}},
    )
    assert tools == ["calculator"]
