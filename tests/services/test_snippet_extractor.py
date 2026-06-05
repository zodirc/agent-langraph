from app.services.snippet_extractor import extract_snippet


def test_code_block_snippet_for_code_fix():
    text = "Intro.\n```python\nERROR_CONNECTION_REFUSED\nraise ConnectionError\n```\nOutro."
    snippet, rng = extract_snippet(text, "ConnectionError fix", purpose="code_fix")
    assert "ConnectionError" in snippet or "ERROR" in snippet
    assert rng is not None


def test_dual_track_summary():
    text = "Long background. " + "word " * 50 + "Python Timsort sorting."
    snippet, _ = extract_snippet(
        text, "Python sort", purpose="fact_qa", max_sentences=2, include_summary=True
    )
    assert "chunk_summary" in snippet or "Python" in snippet
