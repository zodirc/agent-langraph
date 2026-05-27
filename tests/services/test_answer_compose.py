from app.services.answer_compose import compose_user_answer, normalize_code_content


def test_compose_user_answer_merges_artifacts():
    text = compose_user_answer(
        "下面是实现：",
        {
            "artifacts": [
                {
                    "kind": "code",
                    "language": "cpp",
                    "content": "int main() {\n  return 0;\n}\n",
                }
            ]
        },
    )
    assert "下面是实现" in text
    assert "```cpp" in text
    assert "int main()" in text
    assert "return 0;" in text


def test_compose_preserves_leading_indentation():
    indented = "    int x = 1;\n        return x;\n"
    text = compose_user_answer(
        "实现如下：",
        {"artifacts": [{"kind": "code", "language": "cpp", "content": indented}]},
    )
    assert "    int x = 1;" in text
    assert "        return x;" in text


def test_normalize_code_content_keeps_leading_spaces():
    raw = "    line\n"
    assert normalize_code_content(raw).startswith("    ")
