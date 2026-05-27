"""Assemble user-facing answers from reasoning summary and structured artifacts."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings


def preserve_code_whitespace() -> bool:
    raw = getattr(settings, "DISPLAY_CONFIG", None)
    if isinstance(raw, dict):
        compose = raw.get("compose") or {}
        if isinstance(compose, dict) and "preserve_code_whitespace" in compose:
            return bool(compose["preserve_code_whitespace"])
    return True


def normalize_code_content(content: str, *, preserve: bool | None = None) -> str:
    """Preserve leading indentation; only trim a single trailing newline."""
    if not content:
        return ""
    if preserve is False:
        return content.strip()
    if preserve is None:
        preserve = preserve_code_whitespace()
    if not preserve:
        return content.strip()
    return content.rstrip("\n")


def _hide_unverified_code(structured: dict[str, Any]) -> bool:
    raw = getattr(settings, "CODE_ARTIFACT_CONFIG", None)
    if isinstance(raw, dict):
        out = raw.get("output") or {}
        if isinstance(out, dict) and "hide_unverified_code" in out:
            return bool(out["hide_unverified_code"])
    return True


def verify_failure_user_message(structured: dict[str, Any] | None = None) -> str:
    structured = structured or {}
    raw = getattr(settings, "CODE_ARTIFACT_CONFIG", None)
    default = (
        "代码未通过编译校验，本次不展示可能错误的源码。"
        "请重试或查看推理 trace 中的 code_verify_reports / compiler stderr。"
    )
    degraded = (
        "代码未通过编译校验；当前为低风险代码问答场景，已降级返回未校验源码供参考。"
        "使用前请先自行编译验证，并结合 code_verify_reports / compiler stderr 修正。"
    )
    if structured.get("code_verify_degraded"):
        return degraded
    if isinstance(raw, dict):
        out = raw.get("output") or {}
        if isinstance(out, dict) and out.get("failure_message"):
            return str(out["failure_message"])
    return default


def should_emit_verify_failure_message(structured: dict[str, Any] | None) -> bool:
    """True only when compile verify ran and failed for real code artifacts."""
    structured = structured or {}
    if not structured.get("code_verify_failed"):
        return False
    return bool(iter_code_artifact_items(structured))


def should_include_code_artifacts(structured: dict[str, Any] | None) -> bool:
    structured = structured or {}
    if not iter_code_artifact_items(structured):
        return False
    if structured.get("code_verify_ok") is True:
        return True
    if structured.get("code_verify_degraded"):
        return True
    if structured.get("code_verify_failed") and _hide_unverified_code(structured):
        return False
    return True


def iter_code_artifact_items(structured: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in structured.get("artifacts") or []:
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "code":
            out.append(item)
    return out


def compose_user_answer(summary: str, structured: dict[str, Any] | None) -> str:
    """
    Build final markdown for the client.

    Prose lives in summary; code blocks are appended from structured.artifacts
    with whitespace preserved.
    """
    structured = structured or {}
    parts: list[str] = []
    lead = str(summary or "").strip()
    if lead:
        parts.append(lead)

    if should_emit_verify_failure_message(structured):
        if structured.get("code_verify_degraded") or _hide_unverified_code(structured):
            parts.append(verify_failure_user_message(structured))

    if not should_include_code_artifacts(structured):
        if parts:
            return "\n\n".join(parts).strip()
        return lead

    preserve = preserve_code_whitespace()
    for item in structured.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").lower() != "code":
            continue
        content = normalize_code_content(str(item.get("content") or ""), preserve=preserve)
        if not content:
            continue
        lang = str(item.get("language") or "").strip()
        fence_lang = lang if lang else ""
        parts.append(f"```{fence_lang}\n{content}\n```")

    if parts:
        return "\n\n".join(parts).strip()
    return lead


def compose_preview_max_chars() -> int:
    raw = getattr(settings, "DISPLAY_CONFIG", None)
    if isinstance(raw, dict):
        compose = raw.get("compose") or {}
        if isinstance(compose, dict) and compose.get("preview_max_chars") is not None:
            return int(compose["preview_max_chars"])
    return 8000


def compose_user_answer_preview(summary: str, structured: dict[str, Any] | None) -> str:
    """Bounded preview for SSE; does not strip code indentation inside fences."""
    text = compose_user_answer(summary, structured)
    cap = compose_preview_max_chars()
    if len(text) <= cap:
        return text
    return text[:cap]
