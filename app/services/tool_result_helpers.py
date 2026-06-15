"""Safe accessors for heterogeneous tool_results row shapes."""

from __future__ import annotations

from typing import Any, Mapping


def tool_result_body(item: Mapping[str, Any] | dict[str, Any] | None) -> dict[str, Any]:
    """Return the inner tool result dict, never a bare string."""
    if not isinstance(item, Mapping):
        return {}
    raw = item.get("result")
    if isinstance(raw, dict):
        return raw
    return {}


def tool_result_flag(item: Mapping[str, Any] | dict[str, Any] | None, key: str) -> Any:
    """Read a flag from the row or nested result dict."""
    if not isinstance(item, Mapping):
        return None
    if key in item and item.get(key) is not None:
        return item.get(key)
    return tool_result_body(item).get(key)


def _read_line_range_code(result: Mapping[str, Any]) -> str | None:
    """Compact line range label, e.g. L11-21."""
    scope = result.get("scope")
    if isinstance(scope, Mapping) and scope.get("start_line") is not None:
        start = int(scope["start_line"])
        end_raw = scope.get("end_line")
        end = int(end_raw) if end_raw is not None else start
        return f"L{start}-{end}"
    line_count = result.get("line_count")
    if isinstance(line_count, int) and line_count > 0:
        return f"L1-{line_count}"
    return None


def _format_read_tool_preview(result: Mapping[str, Any]) -> str:
    """One-line read summary: 阅读了 {file}，L11-21 — never include body text."""
    from pathlib import Path

    name = str(result.get("filename") or "").strip()
    if not name and result.get("path"):
        name = Path(str(result["path"])).name
    if not name:
        name = "文件"

    line_code = _read_line_range_code(result)
    status = str(result.get("status") or "").strip()
    read_repeat = bool(result.get("read_repeat_blocked")) or status == "cached"

    if line_code:
        summary = f"阅读了 {name}，{line_code}"
    else:
        total_chars = result.get("total_chars")
        if isinstance(total_chars, int) and total_chars > 0:
            summary = f"阅读了 {name}（共 {total_chars:,} 字）"
        else:
            summary = f"阅读了 {name}"

    if read_repeat:
        summary += "（复用缓存）"
    elif result.get("truncated"):
        summary += f"（已截断，共 {result.get('total_chars', '?')} 字）"
    return summary


def format_tool_preview_snippet(tool: str, result: Mapping[str, Any] | dict[str, Any]) -> str:
    """Human-readable preview for SSE tool_preview (reads/edits with line context)."""
    from pathlib import Path

    if not isinstance(result, Mapping):
        return str(result)[:400] if result is not None else ""

    if tool == "read_text_artifact":
        return _format_read_tool_preview(result)

    parts: list[str] = []
    name = str(result.get("filename") or "").strip()
    if not name and result.get("path"):
        name = Path(str(result["path"])).name
    if name:
        parts.append(f"📄 {name}")

    status = str(result.get("status") or "").strip()

    scope = result.get("scope")
    if isinstance(scope, Mapping):
        start = scope.get("start_line")
        end = scope.get("end_line")
        if start is not None:
            end_disp = end if end is not None else start
            parts.append(f"行 {start}–{end_disp}")
    if status == "cached":
        parts.append("(复用上次读取)")
    content = result.get("content")
    if content:
        text = str(content)
        if result.get("truncated"):
            parts.append(f"[已截断，共 {result.get('total_chars', '?')} 字]")
        parts.append(text[:2000])

    if tool == "edit_text_artifact":
        reps = result.get("replacements")
        if reps is not None:
            parts.append(f"✓ 替换 {reps} 处")
        batch = result.get("batch_details")
        if isinstance(batch, list):
            for row in batch[:6]:
                if not isinstance(row, Mapping):
                    continue
                old = str(row.get("old_text") or "")[:40]
                new = str(row.get("new_text") or "")[:40]
                n = row.get("replacements", 0)
                parts.append(f"  · {old} → {new} ({n})")
        diff = result.get("diff_preview")
        if diff:
            parts.append(str(diff)[:1200])

    msg = result.get("message")
    if msg:
        parts.append(str(msg))

    if not parts:
        for key in ("summary", "path", "model_name"):
            val = result.get(key)
            if val:
                parts.append(str(val)[:200])
                break

    return "\n".join(parts).strip()


__all__ = ["format_tool_preview_snippet", "tool_result_body", "tool_result_flag"]
