from app.services.tool_registry import get_tool_registry


def test_tool_registry_invoke(isolated_stores):
    registry = get_tool_registry()
    result = registry.invoke("echo", {"message": "hello"}, user_role="user")
    assert result["tool"] == "echo"
    assert result["result"]["echo"] == "hello"


def test_tool_registry_permission_denied(isolated_stores):
    registry = get_tool_registry()
    try:
        registry.invoke("echo", {"message": "x"}, user_role="guest")
    except PermissionError:
        assert True
    else:
        raise AssertionError("Expected PermissionError")


def test_edit_text_artifact_requires_admin(isolated_stores):
    registry = get_tool_registry()
    try:
        registry.invoke(
            "edit_text_artifact",
            {
                "task_id": "t1",
                "filename": "draft.txt",
                "old_text": "a",
                "new_text": "b",
            },
            user_role="user",
        )
    except PermissionError:
        assert True
    else:
        raise AssertionError("Expected PermissionError")
