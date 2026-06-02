import pytest

from app.services.llm_gateway import (
    adapt_raw_response,
    extract_chunk_stream_parts,
    is_stream_transport_error,
    normalize_message_content,
    _extract_from_text,
    _draft_from_partial_stream,
)
from app.services.artifact_args_parser import ArtifactArgsParser
from app.services.writing_generation import begin_writing_generation


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


def test_is_stream_transport_error():
    assert is_stream_transport_error(Exception("peer closed connection incomplete chunked read"))
    assert is_stream_transport_error(Exception("502 Bad Gateway"))
    assert not is_stream_transport_error(ValueError("bad json"))


def test_draft_from_partial_stream():
    parser = ArtifactArgsParser()
    parser.feed('{"content": "残稿正文')
    gen = begin_writing_generation(task_id="t", filename="n.txt")
    draft = _draft_from_partial_stream(
        parser.accumulated,
        parser,
        None,
        stream_interrupted=True,
        generation=gen,
    )
    assert draft is not None
    assert draft.content == "残稿正文"
    assert draft.meta.get("stream_interrupted") is True


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


def test_adapt_or_retry_thinking_only(monkeypatch):
    from app.services import llm_gateway as gw

    thinking_only = type(
        "Msg",
        (),
        {
            "content": [{"type": "thinking", "thinking": "plan only"}],
            "tool_calls": [],
        },
    )()
    ok = type(
        "Msg",
        (),
        {
            "content": '{"content": "第一章正文"}',
            "tool_calls": [],
        },
    )()

    calls: list[str] = []

    def fake_sync(llm, *, system, user, preferred):
        calls.append(preferred)
        return thinking_only

    def fake_text(llm, system, user):
        calls.append("json_retry")
        return ok

    monkeypatch.setattr(gw, "_invoke_artifact_sync", fake_sync)
    monkeypatch.setattr(gw, "_invoke_text", fake_text)

    draft = gw._adapt_or_retry_thinking_only(
        object(),
        system="sys",
        user="{}",
        preferred="tool",
    )
    assert draft.content == "第一章正文"
    assert calls == ["tool", "json_retry"]
