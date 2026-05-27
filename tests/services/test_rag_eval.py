from app.services.rag_eval import (
    check_faithfulness,
    extract_citations,
    merge_citations,
)


def test_faithful_answer_passes():
    docs = [{"doc_id": "d1", "content": "Python was created by Guido van Rossum"}]
    result = check_faithfulness("Python was created by Guido", docs)
    assert result["faithful"] is True


def test_hallucinated_answer_flagged():
    docs = [{"doc_id": "d1", "content": "Python was created by Guido van Rossum"}]
    result = check_faithfulness("Python was created by Linus Torvalds", docs)
    assert result["faithful"] is False
    assert result["unsupported_claims"]


def test_extract_and_merge_citations():
    docs = [
        {
            "doc_id": "abc-123",
            "title": "Doc",
            "metadata": {"source_url": "https://example.com"},
        }
    ]
    answer = "See [abc-123] for details."
    cites = merge_citations(answer, docs)
    assert cites[0]["doc_id"] == "abc-123"
    assert extract_citations(answer) == ["abc-123"]
