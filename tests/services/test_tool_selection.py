from app.services.tool_registry import get_tool_registry
from app.services.tool_selection import (
    retrieve_relevant_tools,
    validate_tool_selection,
)


def test_retrieve_relevant_tools_prefers_goal_match():
    registry = get_tool_registry()
    tools = retrieve_relevant_tools(
        "calculate math expression",
        "analysis",
        "LOW",
        registry,
        top_k=5,
    )
    names = [t.name for t in tools]
    assert "calculator" in names


def test_retrieve_respects_pack_tools_filter():
    registry = get_tool_registry()
    tools = retrieve_relevant_tools(
        "read artifact",
        "writing",
        "LOW",
        registry,
        top_k=10,
        pack_tools=["read_text_artifact"],
    )
    assert all(t.name == "read_text_artifact" for t in tools)


def test_validate_tool_selection_unknown_tool():
    registry = get_tool_registry()
    valid, issues = validate_tool_selection(
        ["missing_tool_xyz"],
        {},
        registry,
        "user",
    )
    assert valid == []
    assert any("tool_not_found" in i for i in issues)


def test_validate_tool_selection_echo_ok():
    registry = get_tool_registry()
    valid, issues = validate_tool_selection(["echo"], {}, registry, "user")
    assert valid == ["echo"]
    assert issues == []
