from app.runtime.evidence_models import EvidenceConflict, EvidencePacket
from app.services.insufficient_evidence import build_insufficient_response


def test_no_evidence_response():
    r = build_insufficient_response(mode="refuse_if_insufficient", failure_tags=["no_recall"])
    assert r["response_type"] == "no_evidence"


def test_refuse_when_gate_all_filtered():
    r = build_insufficient_response(
        mode="refuse_if_insufficient",
        packets=[EvidencePacket(snippet_text="x")],
        failure_tags=["gate_all_filtered"],
    )
    assert r["response_type"] == "refuse"


def test_conflicting_evidence():
    r = build_insufficient_response(
        conflicts=[EvidenceConflict(field_key="timeout", value_a="30", value_b="60")],
        failure_tags=["evidence_conflict"],
    )
    assert r["response_type"] == "conflicting_evidence"
