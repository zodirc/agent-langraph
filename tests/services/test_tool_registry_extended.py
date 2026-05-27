def test_builtin_tools_registered():
    from app.services.tool_registry import get_tool_registry

    tools = get_tool_registry().list_tools()
    for name in (
        "get_runtime_info",
        "calculator",
        "write_text_artifact",
        "append_text_artifact",
        "read_text_artifact",
        "edit_text_artifact",
    ):
        assert name in tools
