"""LLM content generation for artifact write actions (unified-core WP-6).

When a planned ``write_artifact`` action carries no usable inline content, the
executor calls :func:`generate_artifact_content` to draft it via the LLM
gateway. All manuscript/outline/mission-specific context plumbing was removed;
generation context is the goal, conversation history, and an excerpt of the
target file when it already exists.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.config.settings import settings
from app.services.artifact_resolver import resolve_artifact_target, sanitize_artifact_basename
from app.services.artifact_tools import task_artifact_dir
from app.services.llm_gateway import invoke_artifact_draft, stream_artifact_draft
from app.services.reasoning_trace import report_block, report_status_trace, trace_enabled
from app.services.artifact_stream import artifact_stream_enabled

_PLACEHOLDER_MARKERS = (
    "占位",
    "请在本任务完成后",
    "由助手生成",
    "示例）",
    "章节规划（示例）",
    "推理模块",
    "根据大纲生成",
    "内容由推理",
    "待生成",
    "此处为正文",
)
_SHORT_GOAL_RE = re.compile(r"^(续写|追加|继续|下一章|append)$", re.IGNORECASE)
_CHARS_RE = re.compile(r"(\d+)\s*字")
_WAN_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*万\s*字?")


class SteerPreempted(Exception):
    """Raised when pause/cancel is requested during generation."""


def _check_generation_control(task_id: str, *, step_epoch: int | None = None) -> None:
    from app.services.execution_control import (
        CancelRequested,
        PauseRequested,
        check_for_control_signal,
    )
    from app.services.foreground_execution import EpochStale, get_foreground_epoch

    bound_epoch = step_epoch
    if bound_epoch is None:
        try:
            from app.services.state_store import get_state_store

            stored = get_state_store().load(task_id, read_only=True) or {}
            bound_epoch = get_foreground_epoch(stored)
        except Exception:
            bound_epoch = 0

    try:
        check_for_control_signal(
            str(task_id),
            phase="pre_generate",
            step_epoch=bound_epoch,
            raise_on_pause=True,
            raise_on_cancel=True,
            raise_on_epoch_stale=True,
        )
    except EpochStale as exc:
        raise SteerPreempted(str(exc)) from exc
    except (PauseRequested, CancelRequested) as exc:
        raise SteerPreempted(str(exc)) from exc


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
        try:
            filename = resolve_artifact_target(
                state,
                action="read",
                requested_filename=filename,
                require_exists=False,
            ).filename
        except Exception:
            try:
                filename = sanitize_artifact_basename(filename)
            except ValueError:
                return ""
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
    """Use the LLM gateway to draft file content when the plan supplied none."""
    task_id = str(state["task_id"])
    _check_generation_control(task_id)
    payload = state.get("input_payload") or {}
    history = payload.get("conversation_history") or state.get("conversation_history") or []

    chars = int(target_chars or _effective_target_chars(goal, tool_name))
    from app.services.writing_project import (
        MAX_CHAPTER_CONTINUATION_SEGMENTS,
        chapter_char_count,
        completion_threshold,
        extract_outline_for_chapter,
        is_body_path,
        load_project,
        mark_chapter_continuation_needed,
        merge_body_write_content,
        novel_tail_excerpt,
        read_body_text,
    )

    project = load_project(task_id)
    if project and is_body_path(task_id, filename):
        existing_count = chapter_char_count(task_id, filename)
        chars = max(chars, project.words_per_chapter - existing_count)
    from app.services.writing_project import body_draft_tool_name, kickoff_should_write_not_append

    effective_tool = tool_name
    if project and is_body_path(task_id, filename):
        effective_tool = body_draft_tool_name(task_id, filename)
    if project and is_body_path(task_id, filename) and not kickoff_should_write_not_append(task_id):
        existing_excerpt = novel_tail_excerpt(task_id)
    else:
        existing_excerpt = _read_artifact_snippet(task_id, filename, state=state)

    profile = str(
        payload.get("artifact_profile")
        or (payload.get("route_audit") or {}).get("artifact_profile")
        or ""
    ).lower()
    if not profile:
        from app.services.route_audit.audit import is_code_filename

        if is_code_filename(filename):
            profile = "source_code"
        elif "outline" in filename.lower() or "大纲" in filename:
            profile = "outline"
        else:
            profile = "text"

    if profile == "outline":
        from app.services.artifact_edit_intent import is_artifact_edit_goal

        if is_artifact_edit_goal(goal) and existing_excerpt:
            task_desc = (
                f"Revise the existing OUTLINE in {filename} per the user's goal. "
                f"Output the COMPLETE revised outline (similar length to the original, "
                f"about {chars} characters unless the user asked to shorten/expand). "
                "Use previous_artifact_excerpt as the source material. "
                "Keep outline structure (beats/bullets), not full chapter prose."
            )
        else:
            task_desc = (
                f"Write a complete document OUTLINE in the user's language (markdown), "
                f"about {chars} characters, into {filename}. "
                "Structure it with clear sections the user can expand later: "
                "title, characters, and per-chapter plot beats (bullets or short lines). "
                "Do NOT write full chapter prose, dialogue scenes, or chapter footers "
                "such as （第N章完）. Ignore chapter_index for drafting a single chapter."
            )
    elif profile == "source_code":
        task_desc = (
            f"Write complete source code for the user's goal in file {filename}. "
            "Preserve indentation and newlines. Output code only — no prose, no markdown essay."
        )
    elif effective_tool == "append_text_artifact":
        part = f" (part {chunk_index + 1}/{chunk_total})" if chunk_total > 1 else ""
        word_hint = ""
        if project and is_body_path(task_id, filename):
            written = chapter_char_count(task_id, filename)
            word_hint = (
                f"第{project.next_chapter}章目标字数：{project.words_per_chapter}。"
                f"本章当前已写 {written} 字。"
            )
        task_desc = (
            f"Continue the novel in {filename}{part}: write chapter {project.next_chapter if project else '?'} "
            f"or continue the current chapter, target ~{chars} characters (±10%). {word_hint} "
            "End the chapter with a footer line （第N章完）. Continue immediately after "
            "previous_artifact_excerpt; do NOT repeat earlier chapters or scenes."
        )
    else:
        from app.services.artifact_edit_intent import is_artifact_edit_goal

        if is_artifact_edit_goal(goal) and existing_excerpt:
            task_desc = (
                f"Revise or polish the existing document {filename} per the user's goal. "
                f"Output the COMPLETE revised text (similar length to the original, "
                f"about {chars} characters unless the user asked to shorten/expand). "
                "Use previous_artifact_excerpt as the source material. "
                "No placeholders, no meta commentary — only the final document body."
            )
        else:
            word_hint = ""
            if project and is_body_path(task_id, filename):
                written = chapter_char_count(task_id, filename)
                word_hint = (
                    f"第{project.next_chapter}章目标字数：{project.words_per_chapter}。"
                    f"本章当前已写 {written} 字。"
                )
            task_desc = (
                f"Write chapter {project.next_chapter if project else '?'} for {filename}, "
                f"about {chars} characters. {word_hint} "
                "End with a footer line （第N章完）. Directly satisfy the user's goal. "
                "No filler or meta commentary."
            )

    user_payload = {
        "task_id": task_id,
        "current_goal": goal,
        "filename": filename,
        "tool": effective_tool,
        "target_chars": chars,
        "chunk_index": chunk_index,
        "conversation_history": history[-12:],
        "previous_artifact_excerpt": existing_excerpt or None,
    }
    from app.services.story_bible import should_supplement_source_rag
    from app.services.writing_context import (
        applied_session_source_ids,
        applied_writing_guideline_ids,
        build_session_source_excerpt,
        build_story_bible_excerpt,
        build_writing_guidelines_excerpt,
        format_material_usage_line,
    )

    bible_text, bible_chars, bible_warn = build_story_bible_excerpt(task_id)
    guidelines_excerpt = build_writing_guidelines_excerpt(state)
    source_excerpt = ""
    rag_segments = 0
    bible_source_chars = len(bible_text)
    if should_supplement_source_rag(bible_source_chars):
        from app.services.story_bible import build_source_rag_supplement_excerpt

        rag_excerpt, rag_segments = build_source_rag_supplement_excerpt(state, task_id)
        if rag_excerpt:
            source_excerpt = rag_excerpt
    elif not bible_text:
        rag_excerpt = build_session_source_excerpt(state)
        if rag_excerpt:
            source_excerpt = rag_excerpt
            rag_segments = len(applied_session_source_ids(state))

    writing_ctx = dict(user_payload.get("writing_context") or {})
    if guidelines_excerpt:
        writing_ctx["writing_guidelines_excerpt"] = guidelines_excerpt
    if bible_text:
        writing_ctx["story_bible_excerpt"] = bible_text
    if source_excerpt:
        writing_ctx["session_source_excerpt"] = source_excerpt
    if project and is_body_path(task_id, filename):
        outline_slice = extract_outline_for_chapter(task_id, project.next_chapter)
        novel_tail = novel_tail_excerpt(task_id)
        if outline_slice:
            writing_ctx["outline_for_chapter"] = outline_slice
        if novel_tail:
            writing_ctx["novel_tail"] = novel_tail
        writing_ctx["chapter_index"] = project.next_chapter
        user_payload["chapter_index"] = project.next_chapter
        writing_ctx["continuation_rules"] = (
            "Follow outline_for_chapter for plot beats; continue from novel_tail; "
            "write only chapter_index; end with （第N章完）; do not repeat prior chapters."
        )
    if writing_ctx:
        user_payload["writing_context"] = writing_ctx
    applied_guidelines = applied_writing_guideline_ids(state)
    applied_sources = applied_session_source_ids(state) if source_excerpt else []
    material_usage_line = format_material_usage_line(
        bible_chars=bible_chars,
        rag_segment_count=rag_segments,
        warning=bible_warn,
    )
    from app.services.prompt_context_gateway import (
        context_governance_enabled,
        mutate_state_context_trace,
        prepare_governed_payload,
    )

    if context_governance_enabled():
        user_payload, envelope = prepare_governed_payload(state, "writing", user_payload)
        if isinstance(state, dict):
            mutate_state_context_trace(state, envelope)
    from app.services.artifact_edit_intent import is_artifact_edit_goal

    action_label = "修订" if is_artifact_edit_goal(goal) and existing_excerpt else "生成"
    report_status_trace("writing", f"gateway: {action_label} {filename}（约 {chars} 字）…")

    def _draft_once(desc: str, payload: dict[str, Any]) -> str:
        if trace_enabled() or artifact_stream_enabled():
            draft = stream_artifact_draft(
                purpose="writing",
                task_desc=desc,
                user_payload=payload,
                filename=filename,
                trace_state=state,
            )
        else:
            draft = invoke_artifact_draft(
                purpose="writing",
                task_desc=desc,
                user_payload=payload,
                filename=filename,
                trace_state=state,
            )
        if profile == "source_code":
            return draft.content.rstrip("\n")
        return draft.content.strip()

    content = _draft_once(task_desc, user_payload)
    if project and is_body_path(task_id, filename):
        threshold = completion_threshold(project)
        chapter_prefix_len = chapter_char_count(task_id, filename)
        new_parts = [content.strip()] if content.strip() else []
        combined_len = chapter_prefix_len + (
            len(new_parts[0]) + (2 if chapter_prefix_len and new_parts else 0) if new_parts else 0
        )
        for _seg in range(MAX_CHAPTER_CONTINUATION_SEGMENTS):
            if combined_len >= threshold:
                break
            tail_source = new_parts[-1] if new_parts else (existing_excerpt or "")
            tail = tail_source[-800:] if len(tail_source) > 800 else tail_source
            cont_desc = (
                f"继续写完第{project.next_chapter}章（文件 {filename}）。本章已有内容结尾：\n{tail}\n"
                f"本章目标 {project.words_per_chapter} 字（至少 {threshold} 字），"
                f"当前本章约 {combined_len} 字。章末须保留 （第{project.next_chapter}章完）。"
            )
            more = _draft_once(cont_desc, user_payload)
            if not more.strip():
                break
            new_parts.append(more.strip())
            combined_len += len(more.strip()) + 2
        if effective_tool == "append_text_artifact":
            content = "\n\n".join(new_parts)
        else:
            full_body = read_body_text(task_id, filename)
            content = merge_body_write_content(full_body, "\n\n".join(new_parts))
        if combined_len < threshold:
            mark_chapter_continuation_needed(task_id, filename)
            from app.services.writing_turn_footer import record_writing_turn_metadata

            record_writing_turn_metadata(
                state,
                chapter_shortfall=(
                    f"第{project.next_chapter}章 {combined_len} 字，"
                    f"低于目标 {project.words_per_chapter} 字（需至少 {threshold} 字）。"
                ),
            )
    if not content or needs_generated_content(content, goal):
        raise ValueError(
            f"LLM did not return usable artifact content (adapter_source={draft.source})"
        )
    if trace_enabled():
        preview = content[:200].replace("\n", " ")
        guideline_note = ""
        if applied_guidelines:
            guideline_note = f"\n  风格规范: {', '.join(applied_guidelines)}"
        if applied_sources:
            guideline_note += f"\n  会话素材: {', '.join(applied_sources)}"
        report_block(
            "writing",
            "done",
            f"【写作完成】{filename} — {len(content)} 字\n  开头: {preview}…{guideline_note}\n  {material_usage_line}",
            field="content_preview",
        )
    from app.services.writing_turn_footer import record_writing_turn_metadata

    record_writing_turn_metadata(state, material_usage_line=material_usage_line)

    max_bytes = settings.ARTIFACT_MAX_WRITE_BYTES
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        content = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return content
