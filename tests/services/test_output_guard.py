from app.services.output_guard import evaluate_output, scan_pii


def test_scan_pii_email():
    assert "pii:email" in scan_pii("write to admin@test.org please")


def test_evaluate_output_passes_clean_text():
    result = evaluate_output("Answer: 42", llm_review=False)
    assert result.passed is True


def test_evaluate_output_blocks_pii():
    result = evaluate_output("Email: secret@corp.com", llm_review=False)
    assert result.passed is False
    assert any("pii" in i for i in result.issues)


def test_evaluate_output_passes_code_with_large_integers():
    text = (
        "实现如下：\n```cpp\n"
        "auto x = 123810238103810832013ULL + 2138102381029381092381098230123ULL;\n"
        "```"
    )
    result = evaluate_output(text, llm_review=False)
    assert result.passed is True
