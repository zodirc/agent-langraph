from unittest.mock import patch

from app.nodes.writing_node import writing_node
from app.runtime.state import TaskStatus


def test_writing_node_skipped_when_disabled(base_state):
    state = {
        **base_state,
        "input_payload": {
            **base_state["input_payload"],
            "writing_intent": {"enabled": False},
        },
    }
    result = writing_node(state)
    assert result["current_node"] == "writing"
    assert result["audit_log"][-1]["action"] == "skipped"


def test_writing_node_append_validated(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    state = {
        **base_state,
        "input_payload": {
            **base_state["input_payload"],
            "goal": "继续写第一章",
            "writing_intent": {
                "enabled": True,
                "action": "append_body",
                "target_chars": 500,
                "min_chars": 50,
            },
            "novel_filename": "novel.txt",
        },
        "manuscript": {"body_path": "novel.txt", "body_bytes": 100},
    }
    fake_content = "。" * 300

    with patch(
        "app.nodes.writing_node._generate_validated_content",
        return_value=fake_content,
    ):
        result = writing_node(state)

    assert result["status"] == TaskStatus.WRITTEN.value
    tools = result.get("tool_results") or []
    assert any(t.get("tool") == "append_text_artifact" for t in tools)
    assert result.get("manuscript", {}).get("body_bytes", 0) > 100
