from app.services.retrieval_routing import attach_retrieval_context, purpose_for_llm_node


def test_attach_retrieval_context():
    state = {
        "task_id": "t",
        "session_id": "s",
        "task_type": "qa",
        "skip_retrieval": False,
        "input_payload": {"goal": "How to fix ConnectionError?"},
    }
    ctx = attach_retrieval_context(state)
    assert ctx["retrieval_decision"]["purpose"] == "code_fix"
    assert ctx["query_object"]["standalone_query"]


def test_purpose_for_writing_node():
    state = {"retrieval_decision": {"purpose": "comparative_summary"}}
    assert purpose_for_llm_node(state, "writing") == "planning_background"
