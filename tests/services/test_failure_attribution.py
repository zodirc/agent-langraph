from app.services.failure_attribution import attribute_failures, dashboard_rows


def test_attribute_failures_primary_stage():
    attr = attribute_failures(
        retrieval_trace={"failure_tags": ["no_recall"], "candidate_count": 0},
        grounding_result={"failure_tags": ["fake_citation"], "grounded": False},
    )
    assert attr["primary_stage"] == "recall"
    assert "fake_citation" in attr["failure_tags"]
    rows = dashboard_rows(attr)
    assert any(r["tag"] == "fake_citation" for r in rows)
