from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings

# Meta / capability answers must use the reasoning LLM with get_runtime_info JSON —
# canned summaries here dropped token limits (see answer.log).


def try_fast_reasoning(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Build reasoning_result from deterministic tool output — skips reasoning LLM call.

    Only used when the answer is fully determined (e.g. calculator). Runtime / limit
    questions always fall through to the reasoning model.
    """
    if not settings.FAST_REASONING_ENABLED:
        return None

    tools = state.get("selected_tools") or []
    results = state.get("tool_results") or []
    if not tools or len(tools) != len(results):
        return None

    payloads = [_tool_payload(item) for item in results]
    if any(p is None for p in payloads):
        return None

    if tools == ["get_runtime_info"]:
        return None

    if tools == ["calculator"] and len(payloads) == 1:
        calc = payloads[0]
        expr = calc.get("expression", "")
        value = calc.get("result", "")
        summary = f"{expr} = {value}" if expr else str(value)
        return _result(summary, structured=calc)

    if len(tools) == 1 and tools[0] in (
        "write_text_artifact",
        "append_text_artifact",
        "read_text_artifact",
    ):
        file_info = payloads[0]
        if file_info.get("status") == "ok":
            path = file_info.get("path") or file_info.get("filename", "")
            mode = file_info.get("mode", tools[0])
            summary = f"文件操作完成（{mode}）：{path}"
            if file_info.get("bytes") is not None:
                summary += f"，大小 {file_info['bytes']} 字节。"
            return _result(summary, structured=file_info)

    return None


def _tool_payload(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if item.get("status") in ("error", "skipped"):
        return None
    result = item.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(item, dict) and item.get("model_name"):
        return item
    return None


def _result(summary: str, *, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": summary,
        "confidence": 0.95,
        "risk_level": "LOW",
        "structured": structured,
        "fast_reasoning": True,
    }
