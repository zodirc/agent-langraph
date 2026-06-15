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


def _read_line_range_label(result: Mapping[str, Any]) -> str | None:
    """Human-readable line range for a read_text_artifact result."""
    scope = result.get("scope")
    if isinstance(scope, Mapping) and scope.get("start_line") is not None:
        start = int(scope["start_line"])
        end_raw = scope.get("end_line")
        end = int(end_raw) if end_raw is not None else start
        return f"行 {start}–{end}"
    line_count = result.get("line_count")
    if isinstance(line_count, int) and line_count > 0:
        return f"行 1–{line_count}"
    total_chars = result.get("total_chars")
    if isinstance(total_chars, int) and total_chars > 0:
        return f"共 {total_chars:,} 字"
    return None


def format_tool_preview_snippet(tool: str, result: Mapping[str, Any] | dict[str, Any]) -> str:
    """Human-readable preview for SSE tool_preview (reads/edits with line context)."""
    from pathlib import Path

    if not isinstance(result, Mapping):
        return str(result)[:400] if result is not None else ""

    parts: list[str] = []
    name = str(result.get("filename") or "").strip()
    if not name and result.get("path"):
        name = Path(str(result["path"])).name
    if name:
        parts.append(f"📄 {name}")

    status = str(result.get("status") or "").strip()
    read_repeat = bool(result.get("read_repeat_blocked")) or status == "cached"

    if tool == "read_text_artifact":
        line_label = _read_line_range_label(result)
        if line_label:
            parts.append(line_label)
        if read_repeat:
            parts.append("(复用上次读取，未重复展示正文)")
        elif result.get("truncated"):
            parts.append(f"[已截断，共 {result.get('total_chars', '?')} 字]")
        if not read_repeat and not result.get("with_line_numbers") and not result.get("scope"):
            content = result.get("content")
            if content:
                parts.append(str(content)[:2000])
    else:
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
