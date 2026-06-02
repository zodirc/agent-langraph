from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.config.settings import settings
from app.services.manuscript_context import (
    build_writing_context,
    is_near_duplicate_append,
    read_body_text,
)
from app.services.artifact_tools import task_artifact_dir
from app.services.manuscript_service import resolve_read_paths
from app.services.llm_gateway import invoke_artifact_draft, stream_artifact_draft
from app.services.writing_stream import writing_stream_enabled
from app.services.reasoning_trace import report_block, report_status_trace, trace_enabled

_PLACEHOLDER_MARKERS = ("占位", "请在本任务完成后", "由助手生成", "示例）", "章节规划（示例）")
_SHORT_GOAL_RE = re.compile(r"^(续写|追加|继续|下一章|append)$", re.IGNORECASE)
_CHARS_RE = re.compile(r"(\d+)\s*字")
_WAN_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*万\s*字?")


class SteerPreempted(Exception):
    """Raised when queued steer requests immediate generation stop."""


def parse_requested_chars(goal: str) -> Optional[int]:
    """Parse user target length like 一万字 / 写够8000字."""
    text = goal.strip()
    if not text:
        return None
    if "一万" in text or "1万" in text:
        return 10000
    wan = _WAN_RE.search(text)
    if wan:
        raw = wan.group(1)
        if raw.isdigit():
            return int(raw) * 10000
        if raw in ("一", "1"):
            return 10000
        if raw == "两":
            return 20000
    match = _CHARS_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def needs_generated_content(content: Optional[str], goal: str) -> bool:
    text = (content or "").strip()
    goal_text = goal.strip()
    if not text:
        return True
    if any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return True
    from app.services.manuscript_service import validate_manuscript_content

    ok, _ = validate_manuscript_content(text, action="append_body", min_chars=50)
    if not ok and len(text) < 500:
        return True
    if goal_text and text == goal_text and (len(goal_text) < 40 or _SHORT_GOAL_RE.match(goal_text)):
        return True
    return False


def _read_artifact_snippet(
    task_id: str,
    filename: str,
    max_chars: int = 8000,
    *,
    state: Optional[dict[str, Any]] = None,
) -> str:
    if state:
        filename = resolve_read_paths(state, filename)
    path = task_artifact_dir(task_id) / filename
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...(truncated)"
    return text


def _effective_target_chars(goal: str, tool_name: str) -> int:
    requested = parse_requested_chars(goal)
    default = settings.ARTIFACT_CHUNK_CHARS
    if tool_name == "append_text_artifact" and requested:
        return min(requested, settings.ARTIFACT_MAX_CHARS_PER_TURN)
    if requested:
        return min(requested, settings.ARTIFACT_CHUNK_CHARS)
    return default


def generate_artifact_content(
    *,
    state: dict[str, Any],
    tool_name: str,
    filename: str,
    goal: str,
    target_chars: Optional[int] = None,
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> str:
    """Use LLM to produce outline or chapter text when planning did not supply real content."""
    task_id = str(state["task_id"])
    # Best-effort: if a high-priority steer is queued, avoid starting another long LLM draft.
    try:
        from app.services.mission_steer import (
            has_pending_steer,
            pending_has_forced_action,
            pending_steer_priority,
        )
        from app.services.state_store import get_state_store

        stored = get_state_store().load(task_id, read_only=True) or {}
        pending = stored.get("pending_user_message")
        if pending_has_forced_action(pending, "pause"):
            raise SteerPreempted("forced pause requested")
        if has_pending_steer(task_id) and pending_steer_priority(pending) > 0:
            raise SteerPreempted("steer preempt requested")
    except SteerPreempted:
        raise
    except Exception:
        # Never fail hard if steer inspection breaks; generation will proceed normally.
        pass
    payload = state.get("input_payload") or {}
    history = payload.get("conversation_history") or state.get("conversation_history") or []

    payload = state.get("input_payload") or {}
    outline_name = str(payload.get("outline_filename") or "outline.txt")
    novel_name = str(payload.get("novel_filename") or "novel.txt")
    outline_excerpt = _read_artifact_snippet(task_id, outline_name, state=state)
    intent = payload.get("writing_intent") or {}
    chapter_hint = intent.get("chapter_index")
    writing_ctx = build_writing_context(
        task_id=task_id,
        state=state,
        body_filename=novel_name,
        outline_filename=outline_name,
        chapter_index=int(chapter_hint) if chapter_hint is not None else None,
    )
    chapter_n = writing_ctx["chapter_index"]

    mission = state.get("mission") or {}
    sp = mission.get("step_policy") or {}
    chars = int(
        target_chars
        or intent.get("target_chars")
        or sp.get("chars_per_step")
        or _effective_target_chars(goal, tool_name)
    )

    profile = str(
        payload.get("artifact_profile")
        or (payload.get("route_audit") or {}).get("artifact_profile")
        or ""
    ).lower()
    if not profile:
        from app.services.route_audit.audit import is_code_filename

        if is_code_filename(filename):
            profile = "source_code"
        elif "outline" in filename.lower():
            profile = "outline"
        else:
            profile = "manuscript_prose"

    if profile == "outline" or (
        tool_name == "write_text_artifact" and "outline" in filename.lower()
    ):
        task_desc = (
            f"Write a complete story OUTLINE in Chinese (markdown), about {chars} characters. "
            "Include title, genre, characters, foreshadowing notes per chapter, and "
            "chapter-by-chapter plot beats. Obey writing_context.writing_guidelines_excerpt when present "
            "(natural Chinese prose, anti-AI phrasing, TXT layout rules). "
            "Do NOT write full chapter prose in the outline file."
        )
    elif profile == "source_code":
        task_desc = (
            f"Write complete source code for the user's goal in file {filename}. "
            "Preserve indentation and newlines. Output code only — no story prose, no markdown essay."
        )
    elif tool_name == "append_text_artifact" or profile == "manuscript_prose":
        part = f" (part {chunk_index + 1}/{chunk_total})" if chunk_total > 1 else ""
        task_desc = (
            f"Write ONE new chapter in Chinese{part}: chapter_index={chapter_n}, "
            f"title header ### 第{chapter_n}章 (or equivalent), target ~{chars} characters (±10%). "
            "Continue immediately after novel_tail; obey outline_for_chapter and "
            "writing_context.writing_guidelines_excerpt when present "
            "(plot continuity with novel_tail, de-AI tone, UTF-8 TXT paragraph/dialogue format, "
            "chapter header and footer （第N章完）); "
            "do NOT repeat any scene from novel_tail; do NOT restart earlier chapters."
        )
    else:
        task_desc = (
            f"Write plain text for the user's goal in {filename}, about {chars} characters. "
            "No story prose unless the goal explicitly requests fiction."
        )

    user_payload = {
        "task_id": task_id,
        "current_goal": goal,
        "filename": filename,
        "tool": tool_name,
        "target_chars": chars,
        "chunk_index": chunk_index,
        "chapter_index": chapter_n,
        "conversation_history": history[-12:],
        "existing_outline": outline_excerpt or None,
        "writing_context": writing_ctx,
        "mission": {k: mission.get(k) for k in ("kind", "objective", "step_policy") if mission},
    }
    report_status_trace("writing", f"gateway: 生成 {filename}（约 {chars} 字）…")
    if trace_enabled() or writing_stream_enabled():
        draft = stream_artifact_draft(
            purpose="writing",
            task_desc=task_desc,
            user_payload=user_payload,
            filename=filename,
        )
    else:
        draft = invoke_artifact_draft(
            purpose="writing",
            task_desc=task_desc,
            user_payload=user_payload,
            filename=filename,
        )
    if profile == "source_code":
        content = draft.content.rstrip("\n")
    else:
        content = draft.content.strip()
    if not content or needs_generated_content(content, goal):
        raise ValueError(
            f"LLM did not return usable artifact content (adapter_source={draft.source})"
        )
    if trace_enabled():
        preview = content[:200].replace("\n", " ")
        report_block(
            "writing",
            "done",
            f"【写作完成】{filename} — {len(content)} 字\n  开头: {preview}…",
            field="content_preview",
        )
    max_bytes = settings.ARTIFACT_MAX_WRITE_BYTES
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        content = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return content


def _build_append_chunks(
    state: dict[str, Any],
    goal: str,
    filename: str,
) -> list[str]:
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    per_step = int(
        intent.get("target_chars")
        or (state.get("mission") or {}).get("step_policy", {}).get("chars_per_step")
        or payload.get("chars_per_step")
        or settings.ARTIFACT_CHUNK_CHARS
    )
    budget = min(per_step, settings.ARTIFACT_MAX_CHARS_PER_TURN)
    chunk_size = settings.ARTIFACT_CHUNK_CHARS
    max_chunks = settings.ARTIFACT_MAX_CHUNKS_PER_TURN
    chunks: list[str] = []
    remaining = budget
    rolling_body = read_body_text(str(state["task_id"]), filename, state=state)
    from app.services.stream_progress import report_progress

    from app.services.mission_steer import has_pending_steer

    planned_chunks = max(1, min(max_chunks, (budget + chunk_size - 1) // chunk_size))
    while remaining > 0 and len(chunks) < max_chunks:
        if has_pending_steer(str(state["task_id"])):
            report_status_trace(
                "writing",
                "检测到用户介入排队，分段生成提前结束（已生成段落将 append）",
            )
            break
        this_size = min(remaining, chunk_size)
        report_progress(
            f"正在生成 {filename} 第 {len(chunks) + 1} 段（约 {this_size} 字，剩余目标 {remaining} 字）…"
        )
        report_status_trace(
            "writing",
            f"第 {len(chunks) + 1}/{planned_chunks} 段：约 {this_size} 字，剩余 {remaining} 字",
        )
        tail_chars = int(getattr(settings, "MANUSCRIPT_TAIL_EXCERPT_CHARS", 2400))
        state_for_chunk = state
        if rolling_body.strip():
            payload_mut = dict(state.get("input_payload") or {})
            payload_mut["previous_artifact_excerpt"] = rolling_body[-tail_chars:]
            state_for_chunk = {**state, "input_payload": payload_mut}

        piece = generate_artifact_content(
            state=state_for_chunk,
            tool_name="append_text_artifact",
            filename=filename,
            goal=goal,
            target_chars=this_size,
            chunk_index=len(chunks),
            chunk_total=max_chunks,
        )
        if rolling_body.strip():
            dup, ratio = is_near_duplicate_append(
                rolling_body,
                piece,
                threshold=float(
                    getattr(settings, "MANUSCRIPT_APPEND_DEDUP_RATIO", 0.82)
                ),
            )
            if dup:
                raise ValueError(
                    f"Generated chunk duplicates recent tail (similarity={ratio:.2f}); "
                    "aborting append loop"
                )
        chunks.append(piece)
        remaining = max(0, remaining - len(piece))
        rolling_body = (
            f"{rolling_body.rstrip()}\n\n{piece}" if rolling_body.strip() else piece
        )
    return chunks


def prefill_writing_tool_params(
    state: dict[str, Any],
    payload: dict[str, Any],
    selected_tools: list[str],
) -> dict[str, Any]:
    """
    Generate file content during planning so tool_execution does not block on LLM.
    """
    goal = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()
    tool_params = dict(payload.get("tool_params") or {})
    writing_tools = {"write_text_artifact", "append_text_artifact"}

    try:
        for tool_name in selected_tools:
            if tool_name not in writing_tools:
                continue
            cfg = dict(tool_params.get(tool_name) or {})
            filename = str(
                cfg.get("filename")
                or payload.get("novel_filename")
                or "novel.txt"
            )
            if tool_name == "write_text_artifact" and (
                "outline" in goal.lower() or "大纲" in goal
            ):
                filename = str(payload.get("outline_filename") or "outline.txt")
            if tool_name == "append_text_artifact":
                filename = str(payload.get("novel_filename") or filename)
                requested = parse_requested_chars(goal)
                if requested and requested > settings.ARTIFACT_CHUNK_CHARS:
                    chunks = _build_append_chunks(state, goal, filename)
                    if chunks:
                        payload["append_chunks"] = chunks
                        cfg["content"] = chunks[0]
                    continue
            content = str(cfg.get("content") or "")
            if needs_generated_content(content, goal):
                from app.services.stream_progress import report_progress

                report_progress(f"正在生成 {filename} 正文（约 30–120 秒）…")
                content = generate_artifact_content(
                    state=state,
                    tool_name=tool_name,
                    filename=filename,
                    goal=goal,
                )
            cfg["filename"] = filename
            cfg["content"] = content
            tool_params[tool_name] = cfg
    except Exception as exc:
        payload["prefill_error"] = str(exc)
        payload["force_slow_reasoning"] = False

    if writing_tools.intersection(selected_tools) and "prefill_error" not in payload:
        payload = {**payload, "tool_params": tool_params}
    elif tool_params:
        payload = {**payload, "tool_params": tool_params}
    return payload
