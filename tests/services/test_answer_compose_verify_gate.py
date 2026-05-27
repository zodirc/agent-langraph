from app.services.answer_compose import compose_user_answer, should_include_code_artifacts


def test_hides_code_when_verify_failed():
    structured = {
        "code_verify_failed": True,
        "artifacts": [{"kind": "code", "language": "cpp", "content": "int main(){}"}],
    }
    assert should_include_code_artifacts(structured) is False
    text = compose_user_answer("说明", structured)
    assert "编译校验" in text
    assert "```" not in text


def test_shows_code_when_verify_ok():
    structured = {
        "code_verify_ok": True,
        "artifacts": [{"kind": "code", "language": "cpp", "content": "int x = 1;\n"}],
    }
    assert should_include_code_artifacts(structured) is True
    text = compose_user_answer("说明", structured)
    assert "```cpp" in text
