"""User-visible writing turn footers (material usage, chapter shortfall)."""

from __future__ import annotations

from typing import Any

_MATERIAL_KEY = "writing_material_usage_line"
_SHORTFALL_KEY = "writing_chapter_shortfall"


def record_writing_turn_metadata(
    state: dict[str, Any],
    *,
    material_usage_line: str = "",
    chapter_shortfall: str = "",
) -> None:
    if not isinstance(state, dict):
        return
    payload = dict(state.get("input_payload") or {})
    if material_usage_line:
        payload[_MATERIAL_KEY] = material_usage_line.strip()
    if chapter_shortfall:
        payload[_SHORTFALL_KEY] = chapter_shortfall.strip()
    state["input_payload"] = payload


def writing_turn_footer_lines(state: dict[str, Any]) -> list[str]:
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        return []
    lines: list[str] = []
    material = str(payload.get(_MATERIAL_KEY) or "").strip()
    if material:
        lines.append(material)
    shortfall = str(payload.get(_SHORTFALL_KEY) or "").strip()
    if shortfall:
        lines.append(shortfall)
    reflection = state.get("reflection_result") or {}
    if isinstance(reflection, dict) and reflection.get("source") == "writing_structured":
        for issue in reflection.get("issues") or []:
            text = str(issue).strip()
            if "低于目标" in text and text not in lines:
                lines.append(text)
    return lines


def append_writing_footer(answer: str, state: dict[str, Any]) -> str:
    footer_lines = writing_turn_footer_lines(state)
    if not footer_lines:
        return answer
    body = (answer or "").rstrip()
    block = "\n".join(footer_lines)
    if block in body:
        return body
    return f"{body}\n\n{block}" if body else block
