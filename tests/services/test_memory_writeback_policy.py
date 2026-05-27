from app.runtime.state import TaskStatus
from app.services.memory_writeback_policy import should_index_episode


def test_skip_rejected_status():
    state = {"status": TaskStatus.REJECTED.value, "input_payload": {}}
    assert should_index_episode(state) is False


def test_skip_parser_fallback():
    state = {
        "status": TaskStatus.COMPLETED.value,
        "reasoning_result": {"structured": {"parser_fallback": True}},
        "input_payload": {},
        "final_answer": "ok",
    }
    assert should_index_episode(state) is False


def test_indexes_normal_completed():
    state = {
        "status": TaskStatus.COMPLETED.value,
        "reasoning_result": {"structured": {}},
        "input_payload": {"route_audit": {"inferred_kind": "code"}},
        "final_answer": "int main() {\n  return 0;\n}\n",
    }
    assert should_index_episode(state) is True
