from app.services.content_segments import prose_text, split_content_segments
from app.services.output_guard import scan_pii


def test_split_fenced_code_segment():
    text = "说明如下：\n```cpp\nint main() {}\n```\n结束"
    segments = split_content_segments(text)
    kinds = [s.kind for s in segments]
    assert "fenced_code" in kinds
    assert "prose" in kinds


def test_pii_scan_skips_long_digits_inside_code_fence():
    body = (
        "示例：\n```cpp\n"
        "unsigned long a = 1231231231823012830123ULL;\n"
        "unsigned long b = 12312381092381023123123123ULL;\n"
        "```"
    )
    assert scan_pii(body, prose_only=True) == []


def test_pii_scan_still_finds_email_in_prose():
    text = "联系我 admin@test.org\n```cpp\nint x=1;\n```"
    issues = scan_pii(text, prose_only=True)
    assert "pii:email" in issues


def test_prose_text_excludes_code():
    segments = split_content_segments("hello\n```\n123456789012345678901\n```")
    assert "123456789012345678901" not in prose_text(segments)
