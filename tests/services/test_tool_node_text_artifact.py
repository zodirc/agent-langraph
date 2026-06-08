from app.services.artifact_resolver import resolve_artifact_target
from app.runtime.state import create_initial_state, merge_state


def test_resolve_artifact_ignores_cpp_body_path():
    state = merge_state(
        create_initial_state(task_id="t-art-1"),
        input_payload={
            "manuscript": {"body_path": "main.cpp"},
        },
    )
    with __import__("pytest").raises(Exception):
        resolve_artifact_target(
            state,
            action="read",
            target_hint="body",
            require_exists=True,
        )
