from app.services.mission_executor import _chapter_index_mismatch_note


def test_chapter_index_mismatch_note_detects_header_vs_cursor():
    note = _chapter_index_mismatch_note(
        chapter_cursor=32,
        outcome={"chapter_summary": "### 第31章 观测者的黎明\n\n清晨…"},
    )
    assert note is not None
    assert "31" in note
    assert "32" in note


def test_chapter_index_mismatch_note_absent_when_aligned():
    note = _chapter_index_mismatch_note(
        chapter_cursor=31,
        outcome={"chapter_summary": "### 第31章 标题"},
    )
    assert note is None
