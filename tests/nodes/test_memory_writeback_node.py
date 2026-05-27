from app.nodes.memory_writeback_node import memory_writeback_node
from app.nodes.output_node import output_node
from app.runtime.state import TaskStatus, merge_state


def test_memory_writeback_persists_memory(base_state, isolated_stores):
    import app.services.memory_store as memory_mod

    memory_mod._store = None
    from app.services.memory_store import get_memory_store

    state = merge_state(
        base_state,
        reasoning_result={"summary": "memory test", "confidence": 0.8, "risk_level": "LOW"},
        policy_result="CONTINUE",
    )
    completed = output_node(state)
    result = memory_writeback_node(completed)
    assert result["audit_log"][-1]["node"] == "memory_writeback"
    hits = get_memory_store().search("memory test", user_id="tester")
    assert len(hits) >= 1, f"expected memory hit, store path={get_memory_store().db_path}"
