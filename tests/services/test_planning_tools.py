from app.services.planning_tools import normalize_selected_tools


def test_normalize_drops_search_alias():
    accepted, dropped = normalize_selected_tools(["search", "echo"])
    assert accepted == ["echo"]
    assert "search" in dropped


def test_normalize_drops_unknown():
    accepted, dropped = normalize_selected_tools(["nonexistent_tool"])
    assert accepted == []
    assert dropped == ["nonexistent_tool"]
