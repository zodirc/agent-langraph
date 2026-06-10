"""P1-2 evidence fidelity rate."""

from app.services.context_items import ContextItem, new_context_id
from app.services.evidence_fidelity import compute_evidence_fidelity


def test_evidence_fidelity_rate():
    state = {
        "evidence_packets": [
            {"packet_id": "p1", "relevance_score": 0.9},
            {"packet_id": "p2", "relevance_score": 0.8},
            {"packet_id": "p3", "relevance_score": 0.2},
        ]
    }
    kept = [
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content="a",
            meta={"evidence_packet_id": "p1"},
            bucket="retrieved_knowledge",
        ),
        ContextItem(
            id=new_context_id(),
            kind="knowledge",
            source="retrieval",
            content="b",
            meta={"evidence_packet_id": "p2"},
            bucket="retrieved_knowledge",
        ),
    ]
    result = compute_evidence_fidelity(state, kept, score_floor=0.35)
    assert result["high_score_total"] == 2
    assert result["high_score_kept"] == 2
    assert result["rate"] == 1.0
