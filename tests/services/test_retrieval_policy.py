from app.services.retrieval_policy import (
    needs_session_memory_retrieval,
    should_route_to_retrieval_after_planning,
)


def test_needs_session_memory_when_multi_turn():
    state = {
        "session_turn": 3,
        "input_payload": {"conversation_history": []},
    }
    assert needs_session_memory_retrieval(state) is True


def test_should_route_when_skip_retrieval_but_session_memory_needed():
    state = {
        "skip_retrieval": True,
        "session_turn": 2,
        "input_payload": {},
    }
    assert should_route_to_retrieval_after_planning(state) is True
