import json
from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus


def test_planning_strips_hallucinated_search_tool(base_state):
    llm_result = {
        "plan": ["retrieve_knowledge", "answer"],
        "selected_tools": ["search"],
        "risk_level": "LOW",
    }
    stream_payload = json.dumps(llm_result, ensure_ascii=False)

    def fake_stream(*_args, **_kwargs):
        yield stream_payload

    with patch("app.nodes.planning_node.stream_structured", side_effect=fake_stream):
        result = planning_node(base_state)
    assert result["status"] == TaskStatus.PLANNED.value
    assert result.get("selected_tools") == []
