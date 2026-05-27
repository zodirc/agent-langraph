import pytest

from app.services.llm_gateway import (
    adapt_raw_response,
    extract_chunk_stream_parts,
    normalize_message_content,
    _extract_from_text,
)


def test_extract_chunk_stream_parts_splits_blocks():
    t, x = extract_chunk_stream_parts(
        [
            {"type": "thinking", "thinking": "reason"},
            {"type": "text", "text": '{"summary":"ok"}'},
        ]
    )
    assert t == "reason"
    assert x == '{"summary":"ok"}'


def test_normalize_skips_thinking_blocks():
    content = [
        {"type": "thinking", "thinking": "用户要求"},
        {"type": "text", "text": '{"content": "hello"}'},
    ]
    text, meta = normalize_message_content(content)
    assert "用户要求" not in text
    assert "hello" in text or "content" in text
    assert meta.get("thinking_snippets")


def test_adapt_rejects_thinking_only():
    raw = type(
        "Msg",
        (),
        {
            "content": [
                {"type": "thinking", "thinking": "plan"},
            ],
            "tool_calls": [],
        },
    )()
    with pytest.raises(ValueError, match="thinking blocks"):
        adapt_raw_response(raw)


def test_extract_json_content():
    draft = _extract_from_text('{"content": "第一章正文"}')
    assert draft.content == "第一章正文"
    assert draft.source == "json_text"


def test_adapt_tool_calls():
    raw = type(
        "Msg",
        (),
        {
            "content": [],
            "tool_calls": [
                {"name": "submit_artifact", "args": {"content": "章节内容"}},
            ],
        },
    )()
    draft = adapt_raw_response(raw)
    assert draft.content == "章节内容"
    assert draft.source == "tool"
