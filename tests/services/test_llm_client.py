from app.services.llm_client import (
    RetryableError,
    _extract_json,
    extract_json_with_repair,
    _normalize_anthropic_base_url,
    invoke_structured,
    normalize_planning_plan,
    stream_structured,
    with_retry,
)
from app.services.llm_gateway import extract_chunk_stream_parts


def test_normalize_anthropic_base_url_strips_messages_suffix():
    assert (
        _normalize_anthropic_base_url("https://proxy.example/v1/messages")
        == "https://proxy.example"
    )
    assert _normalize_anthropic_base_url("https://proxy.example") == "https://proxy.example"


def test_extract_chunk_stream_parts_thinking_vs_text():
    thinking, text = extract_chunk_stream_parts(
        [{"type": "thinking", "thinking": "Let me think"}]
    )
    assert thinking == "Let me think"
    assert text == ""
    thinking2, text2 = extract_chunk_stream_parts(
        [{"type": "text", "text": '{"summary":"hi"}'}]
    )
    assert thinking2 == ""
    assert "summary" in text2


def test_extract_json_with_trailing_text():
    raw = '{"plan":["a"],"selected_tools":[]} Some extra explanation from the model.'
    result = _extract_json(raw)
    assert result["plan"] == ["a"]


def test_extract_json_markdown_fence():
    raw = '```json\n{"summary":"ok","confidence":0.9}\n```\nDone.'
    result = _extract_json(raw)
    assert result["summary"] == "ok"


def test_extract_json_two_objects_takes_first():
    raw = '{"plan":["step1"]} {"plan":["step2"]}'
    result = _extract_json(raw)
    assert result["plan"] == ["step1"]


def test_extract_json_prefers_summary_across_objects():
    raw = (
        '{"confidence":0.7,"risk_level":"LOW","structured":{"reasoning_mode":"direct"}} '
        '{"summary":"我能做文件操作与推理分析","confidence":0.7}'
    )
    result = _extract_json(raw, prefer_keys=("summary",))
    assert result["summary"] == "我能做文件操作与推理分析"
    assert result["structured"]["reasoning_mode"] == "direct"


def test_extract_json_merge_plan_when_preferred():
    raw = '{"risk_level":"LOW"} {"plan":["step1","step2"],"selected_tools":[]}'
    result = _extract_json(raw, prefer_keys=("plan",))
    assert result["plan"] == ["step1", "step2"]


def test_extract_json_merge_mission_and_writing_intent():
    raw = (
        '{"plan":["outline"]} '
        '{"mission":{"kind":"writing","total_target_chars":1000},'
        '"writing_intent":{"enabled":true,"action":"write_outline"}}'
    )
    result = _extract_json(raw, prefer_keys=("plan", "writing_intent", "mission"))
    assert result["mission"]["kind"] == "writing"
    assert result["writing_intent"]["action"] == "write_outline"


def test_normalize_planning_plan_collapses_append_spam():
    raw = ["write_outline"] + ["append_body"] * 20
    plan = normalize_planning_plan(raw)
    assert len(plan) <= 12
    assert plan[0] == "write_outline"
    assert sum(1 for s in plan if "append" in s.lower()) <= 2


def test_extract_json_recovers_truncated_planning():
    truncated = (
        '{"plan":["write_outline","append_body","append_body","append_body","append_body",'
        '"append_body","append_body","append_body","append_body","append_body","append_body",'
        '"append_body","append'
    )
    result = _extract_json(truncated, prefer_keys=("plan",))
    assert result["plan"]
    assert result["plan"][0] == "write_outline"
    assert len(result["plan"]) <= 12


def test_extract_json_with_repair_reasoning_fallback_for_codeblock_text():
    raw = "```cpp\n#include <iostream>\nint main(){return 0;}\n```"
    result = extract_json_with_repair("reasoning", raw, prefer_keys=("summary",))
    assert "summary" in result
    assert "int main()" in result["summary"]
    assert result["structured"]["parser_fallback"] is True


def test_extract_json_with_repair_non_reasoning_still_raises():
    raw = "```cpp\nint main(){return 0;}\n```"
    try:
        extract_json_with_repair("planning", raw, prefer_keys=("plan",))
    except ValueError:
        assert True
    else:
        assert False, "planning parse should still fail on non-JSON output"


def test_invoke_structured_local_mode(test_settings):
    result = invoke_structured(
        "planning",
        "system",
        '{"goal": "build agent", "risk_level": "LOW"}',
    )
    assert "plan" in result
    assert isinstance(result["plan"], list)


def test_stream_structured_local_mode(test_settings):
    chunks = list(
        stream_structured(
            "reasoning",
            "system",
            '{"goal": "explain agent", "risk_level": "LOW"}',
        )
    )
    assert chunks
    assert "Processed goal" in "".join(chunks)


def test_retry_decorator_raises_after_max():
    calls = {"count": 0}

    @with_retry(max_retries=2, base_delay=0.01, backoff=1.0)
    def flaky() -> str:
        calls["count"] += 1
        raise RetryableError("temporary")

    try:
        flaky()
    except RetryableError:
        assert calls["count"] == 2
