from app.services.manuscript_context import (
    cn_numeral_to_int,
    extract_outline_chapter_brief,
    is_near_duplicate_append,
    parse_last_chapter_index,
    split_paragraphs,
)


def test_cn_numeral_to_int():
    assert cn_numeral_to_int("三") == 3
    assert cn_numeral_to_int("11") == 11
    assert cn_numeral_to_int("十一") == 11


def test_parse_last_chapter_index():
    body = "### 第一章\n\nfoo\n\n### 第三章\n\nbar\n"
    assert parse_last_chapter_index(body) == 3


def test_extract_outline_chapter_brief():
    outline = "# 标题\n\n### 第一章\n情节A\n\n### 第二章\n情节B\n伏笔X\n"
    brief = extract_outline_chapter_brief(outline, 2)
    assert "第二章" in brief
    assert "伏笔X" in brief
    assert "【本章大纲" in brief


def test_is_near_duplicate_append():
    tail = "克劳斯在慕尼黑遇到了汉斯。" * 20
    dup = tail[-400:]
    yes, ratio = is_near_duplicate_append(tail, dup)
    assert yes is True
    assert ratio >= 0.82
    no, _ = is_near_duplicate_append(tail, "完全不同的新场景描写。" * 30)
    assert no is False


def test_split_paragraphs():
    text = "第一段内容足够长。" * 5 + "\n\n" + "第二段内容足够长。" * 5
    parts = split_paragraphs(text, min_len=10)
    assert len(parts) == 2
