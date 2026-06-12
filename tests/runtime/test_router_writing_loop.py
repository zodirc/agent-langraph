"""Router must not re-run fulfilled writing plans in a tight tool loop."""

from app.runtime.router import route_after_tool
from app.runtime.state import create_initial_state


def test_route_after_tool_does_not_repeat_fulfilled_plan():
    state = create_initial_state(
        task_id="router-loop",
        input_payload={
            "goal": "请开始正文写作，写1-10章",
            "writing_intent": {"enabled": True},
            "turn_contract": {
                "primary_op": "write_artifact",
                "tools": ["read_text_artifact", "write_text_artifact"],
            },
            "planned_actions": [
                {"type": "read_artifact", "params": {"filename": "大纲.md"}},
                {"type": "write_artifact", "params": {"filename": "正文/novel.md"}},
            ],
            "selected_tools": ["read_text_artifact", "write_text_artifact"],
        },
    )
    state["tool_results"] = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"filename": "大纲.md"}},
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"filename": "正文/novel.md", "bytes": 1200},
        },
    ]
    state["turn_facts"] = {"write_verified": True, "tools_executed": [{"tool": "write_text_artifact"}]}

    assert route_after_tool(state) != "tool_execution"
