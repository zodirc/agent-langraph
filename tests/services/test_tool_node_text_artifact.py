from app.nodes.tool_node import _resolve_artifact_filename
from app.runtime.state import create_initial_state, merge_state


def test_resolve_artifact_ignores_cpp_body_path():
    state = merge_state(
        create_initial_state(task_id="t-art-1"),
        input_payload={
            "manuscript": {"body_path": "main.cpp"},
            "novel_filename": "main.cpp",
        },
    )
    name = _resolve_artifact_filename(
        state, tool_name="read_text_artifact", requested_filename=""
    )
    assert name.endswith((".md", ".txt"))
