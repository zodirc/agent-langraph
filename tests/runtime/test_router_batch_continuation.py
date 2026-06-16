"""Router fast-path for in-turn writing batch continuation."""

from app.runtime.router import route_after_tool
from app.runtime.state import TaskStatus, create_initial_state


def test_route_after_tool_continues_ready_batch():
    state = create_initial_state(
        task_id="router-batch",
        input_payload={
            "goal": "写到第6章",
            "writing_intent": {"enabled": True},
            "thin_execution_profile": "writing_batch",
            "writing_batch_chapters_written": 2,
        },
    )
    state = {
        **state,
        "status": TaskStatus.PLANNED.value,
        "planned_actions": [
            {
                "type": "run_tool",
                "params": {"name": "append_text_artifact", "filename": "正文/novel.md"},
            }
        ],
        "tool_results": [
            {
                "tool": "append_text_artifact",
                "status": "ok",
                "result": {"filename": "正文/novel.md"},
            }
        ],
    }
    assert route_after_tool(state) == "tool_execution"
