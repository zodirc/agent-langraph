from app.services.evidence_hierarchy import collect_unified_evidence, layer_rank


def test_layer_rank_order():
    assert layer_rank("user_input") > layer_rank("tool_result")
    assert layer_rank("tool_result") > layer_rank("memory")


def test_collect_unified_evidence():
    state = {
        "input_payload": {"goal": "user question"},
        "tool_results": [{"tool": "search", "result": "tool output data"}],
        "evidence_packets": [
            {"packet_id": "p1", "snippet_text": "kb fact", "chunk_id": "c1", "source_type": "knowledge"}
        ],
        "memory_hits": [{"id": "m1", "summary": "memory fact"}],
    }
    packets = collect_unified_evidence(state)
    assert packets[0].source_type == "user_input"
    types = {p.source_type for p in packets}
    assert "tool_result" in types
    assert "knowledge" in types
