from app.services.reasoning_trace import extract_artifact_code_content


def test_extract_artifact_code_from_partial_json():
    raw = (
        '{"summary":"说明","structured":{"artifacts":[{"kind":"code",'
        '"language":"cpp","content":"    int x;\\n"}}'
    )
    text = extract_artifact_code_content(raw)
    assert text.startswith("    int")
