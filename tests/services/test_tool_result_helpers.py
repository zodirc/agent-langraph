from app.services.tool_result_helpers import format_tool_preview_snippet, tool_result_body, tool_result_flag


def test_tool_result_body_ignores_string_result():
    assert tool_result_body({"result": "oops"}) == {}


def test_tool_result_flag_from_nested():
    row = {"result": {"non_retryable": True}}
    assert tool_result_flag(row, "non_retryable") is True


def test_format_read_preview_with_lines():
    snippet = format_tool_preview_snippet(
        "read_text_artifact",
        {
            "filename": "chapter_01.md",
            "scope": {"start_line": 10, "end_line": 20},
            "content": "   1| 梁致远走进办公室\n   2| 秦池在等他",
            "with_line_numbers": True,
            "line_count": 120,
        },
    )
    assert snippet == "阅读了 chapter_01.md，L10-20"
    assert "梁致远" not in snippet


def test_format_read_full_file_preview():
    snippet = format_tool_preview_snippet(
        "read_text_artifact",
        {
            "filename": "大纲.md",
            "content": "很长的大纲正文" * 200,
            "line_count": 88,
            "total_chars": 5000,
        },
    )
    assert snippet == "阅读了 大纲.md，L1-88"
    assert "很长" not in snippet


def test_format_read_cached_shows_lines_only():
    snippet = format_tool_preview_snippet(
        "read_text_artifact",
        {
            "filename": "正文/novel.md",
            "status": "cached",
            "read_repeat_blocked": True,
            "line_count": 450,
            "content": "很长的正文" * 500,
        },
    )
    assert snippet == "阅读了 正文/novel.md，L1-450（复用缓存）"
    assert "很长的正文" not in snippet


def test_format_edit_preview():
    snippet = format_tool_preview_snippet(
        "edit_text_artifact",
        {
            "filename": "outline.md",
            "replacements": 3,
            "batch_details": [{"old_text": "秦池", "new_text": "秦梅", "replacements": 2}],
            "diff_preview": "-秦池\n+秦梅",
        },
    )
    assert "替换 3 处" in snippet
    assert "秦池" in snippet
