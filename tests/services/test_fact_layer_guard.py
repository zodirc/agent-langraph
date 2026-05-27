from app.services.fact_layer import validate_reasoning_summary


def test_validate_chapter_mismatch():
    warnings = validate_reasoning_summary(
        "已完成第 5 章写作",
        {
            "executed_actions": ["writing:append_body"],
            "tools_executed": [],
            "manuscript": {
                "body_bytes": 10000,
                "chapter_cursor": 3,
                "last_chapter_index": 2,
            },
            "writing_intent": {"chapter_index": 5},
            "progress_metrics": {"written_chars": 5000},
        },
    )
    assert any("chapter" in w.lower() or "chapter_index" in w for w in warnings)
