from app.services.manuscript_context import (
    _OUTLINE_CONTINUATION_RULES,
    build_writing_context,
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


def test_build_writing_context_outline_mode():
    state = {
        "task_id": "ctx-outline",
        "input_payload": {
            "writing_intent": {"action": "write_outline"},
            "goal": "写基督山大纲",
        },
    }
    ctx = build_writing_context(
        task_id="ctx-outline",
        state=state,
        body_filename="基督山新传.txt",
        outline_filename="基督山新传_大纲.txt",
    )
    assert ctx["writing_mode"] == "outline"
    assert ctx["novel_tail"] is None
    assert ctx["continuation_rules"] == list(_OUTLINE_CONTINUATION_RULES)
    assert not any("End with a single chapter footer" in r for r in ctx["continuation_rules"])
