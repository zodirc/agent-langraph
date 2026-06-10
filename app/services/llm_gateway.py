"""LLM Gateway — 协议层适配
节点 Nodes call gateway instead of raw LangChain for metrics, retry, structured modes.

Protocol-level LLM response adaptation (blocks, tools, schema).
Writing may use submit_artifact tool; falls back to JSON-in-text when disabled."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from app.services.metrics_service import get_metrics_service

from app.config.settings import settings
from app.services.llm_capabilities import build_model_capabilities
from app.services.llm_client import (
    RetryableError,
    _extract_json,
    _strip_markdown_fences,
    get_llm,
    with_retry,
)
from app.services.reasoning_trace import (
    extract_field_text,
    report_status_trace,
    stream_llm_trace,
    thinking_stream_enabled,
    trace_enabled,
)
from app.services.artifact_args_parser import ArtifactArgsParser
from app.services.circuit_breaker import classify_llm_error, is_retryable_category
from app.services.stream_progress import report_thinking_delta
from app.services.generation_record import (
    GenerationRecord,
    begin_generation,
    map_stream_close_status,
)
from app.services.artifact_stream import (
    artifact_stream_enabled,
    emit_artifact_content_deltas,
    emit_full_artifact_content_deltas,
    extract_streaming_content,
    maybe_report_artifact_buffer_trace,
    report_artifact_stream_done,
    report_artifact_stream_start,
)

# Protocol: content blocks the gateway understands (not domain keywords).
_SKIP_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking", "thinking_delta"})
_TEXT_BLOCK_TYPES = frozenset({"text"})
_TOOL_BLOCK_TYPES = frozenset({"tool_use"})

ARTIFACT_TOOL_NAME = "submit_artifact"


def _usage_from_llm_response(response: Any) -> tuple[int | None, dict[str, int] | None]:
    from app.services.llm_client import _extract_usage_detail

    detail = _extract_usage_detail(response)
    if not detail:
        return None, None
    return int(detail["total_tokens"]), detail


def _log_gateway_llm_interaction(
    *,
    purpose: str,
    system: str,
    user: str,
    response_text: str,
    user_payload: dict[str, Any],
    status: str = "ok",
    trace_state: Any | None = None,
    billed_tokens: int | None = None,
    usage_detail: dict[str, int] | None = None,
    llm_response: Any | None = None,
) -> None:
    from app.services.llm_client import _record_llm_usage
    from app.services.llm_interaction_store import record_llm_interaction

    if llm_response is not None and (billed_tokens is None or usage_detail is None):
        extracted_total, extracted_detail = _usage_from_llm_response(llm_response)
        if billed_tokens is None:
            billed_tokens = extracted_total
        if usage_detail is None:
            usage_detail = extracted_detail
    if isinstance(trace_state, dict) and usage_detail:
        trace_state["_last_llm_usage_detail"] = usage_detail

    task_id = str(user_payload.get("task_id") or "")
    record_llm_interaction(
        trace_state=trace_state,
        purpose=purpose,
        system_prompt=system,
        user_content=user,
        response_text=response_text,
        status=status,
        source="llm_gateway",
        task_id=task_id or None,
        session_id=task_id or None,
    )
    if isinstance(trace_state, dict) and trace_state.get("task_id"):
        _record_llm_usage(
            purpose=purpose,
            system_prompt=system,
            user_content=user,
            response_text=response_text,
            trace_state=trace_state,
            billed_tokens=billed_tokens,
            update_session_budget=True,
        )
ARTIFACT_TOOL_SCHEMA = {
    "name": ARTIFACT_TOOL_NAME,
    "description": "Submit finalized text to persist as the task artifact file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Full plain text to save (story/outline prose only).",
            },
        },
        "required": ["content"],
    },
}


@dataclass
class ArtifactDraft:
    """Normalized artifact payload after adapter pipeline."""

    content: str
    source: str  # tool | json_text | text_only
    meta: dict[str, Any] = field(default_factory=dict)
    raw_length: int = 0

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "content_chars": len(self.content),
            "meta": self.meta,
        }


def normalize_message_content(content: Any) -> tuple[str, dict[str, Any]]:
    """
    Map provider message content to (text_for_parser, meta).

    Thinking blocks are recorded in meta, never concatenated into parser input.
    """
    meta: dict[str, Any] = {"blocks": []}

    if content is None:
        return "", meta

    if isinstance(content, str):
        return content.strip(), meta

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = str(block.get("type") or "")
                meta["blocks"].append({"type": btype})
                if btype in _SKIP_BLOCK_TYPES:
                    meta.setdefault("thinking_snippets", []).append(
                        str(block.get("thinking") or block.get("text") or "")[:200]
                    )
                    continue
                if btype in _TOOL_BLOCK_TYPES:
                    meta.setdefault("tool_blocks", []).append(block)
                    continue
                if btype in _TEXT_BLOCK_TYPES or "text" in block:
                    text_parts.append(str(block.get("text") or ""))
                    continue
                if "text" in block:
                    text_parts.append(str(block["text"]))
                    continue
                continue
            if hasattr(block, "type"):
                btype = str(getattr(block, "type", ""))
                if btype in _SKIP_BLOCK_TYPES:
                    continue
                if hasattr(block, "text"):
                    text_parts.append(str(block.text))
                continue
            text_parts.append(str(block))
        return "\n".join(p for p in text_parts if p).strip(), meta

    return str(content).strip(), meta


def extract_chunk_stream_parts(content: Any) -> tuple[str, str]:
    """
    Split one streamed AIMessageChunk into (thinking_delta, text_delta).

    Provider proxies emit separate content blocks (thinking vs text). Only text
    should feed structured JSON parsers and answer_delta summary extraction.

    IMPORTANT: We must NOT strip the text_delta because streaming tokens may
    carry meaningful whitespace (e.g. ``" SINGLETON_H"`` following ``"#ifndef"``).
    Stripping would collapse them into ``"#ifndefSINGLETON_H"``.
    """
    if content is None:
        return "", ""
    if isinstance(content, str):
        if not content:
            return "", ""
        # Pure-whitespace chunks (e.g. "\n", " ") carry meaningful inter-token
        # spacing in code/JSON streams; we must NOT drop them.
        stripped_check = content.strip()
        if not stripped_check:
            # whitespace-only: still pass through as text to preserve spacing
            return "", content
        if stripped_check.startswith("{") and '"thinking"' in stripped_check.lower()[:80]:
            return content, ""
        return "", content

    thinking_parts: list[str] = []
    text_parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                btype = str(block.get("type") or "")
                if btype in _SKIP_BLOCK_TYPES:
                    thinking_parts.append(str(block.get("thinking") or block.get("text") or ""))
                    continue
                if btype in _TEXT_BLOCK_TYPES or "text" in block:
                    text_parts.append(str(block.get("text") or ""))
                    continue
                if "text" in block:
                    text_parts.append(str(block["text"]))
                continue
            if hasattr(block, "type"):
                btype = str(getattr(block, "type", ""))
                if btype in _SKIP_BLOCK_TYPES:
                    piece = str(
                        getattr(block, "thinking", None)
                        or getattr(block, "text", None)
                        or ""
                    )
                    if piece:
                        thinking_parts.append(piece)
                    continue
                if hasattr(block, "text"):
                    text_parts.append(str(block.text))
                continue
    return "".join(thinking_parts), "".join(text_parts)


def _extract_from_tool_blocks(meta: dict[str, Any]) -> Optional[ArtifactDraft]:
    for block in meta.get("tool_blocks") or []:
        name = str(block.get("name") or "")
        if name != ARTIFACT_TOOL_NAME:
            continue
        inp = block.get("input") or block.get("args") or {}
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except json.JSONDecodeError:
                continue
        if isinstance(inp, dict):
            content = str(inp.get("content") or "").strip()
            if content:
                return ArtifactDraft(
                    content=content,
                    source="tool",
                    meta={"tool": name},
                )
    return None


def _extract_from_text(normalized: str) -> ArtifactDraft:
    cleaned = _strip_markdown_fences(normalized)
    try:
        obj = _extract_json(cleaned)
        content = str(obj.get("content") or obj.get("summary") or "").strip()
        if content:
            return ArtifactDraft(content=content, source="json_text", meta={"keys": list(obj)})
    except ValueError:
        pass
    if cleaned and not cleaned.startswith("{"):
        return ArtifactDraft(content=cleaned, source="text_only", meta={})
    raise ValueError("Could not extract artifact content from model output")


def _extract_from_aimessage_tool_calls(response: Any) -> Optional[ArtifactDraft]:
    tool_calls = getattr(response, "tool_calls", None) or []
    for tc in tool_calls:
        name = str(tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""))
        if name != ARTIFACT_TOOL_NAME:
            continue
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
        if isinstance(args, dict):
            content = str(args.get("content") or "").strip()
            if content:
                return ArtifactDraft(content=content, source="tool", meta={"tool": name})
    return None


def adapt_raw_response(raw: Any) -> ArtifactDraft:
    """Full adapter pipeline: normalize blocks → tool → json → plain text."""
    draft = _extract_from_aimessage_tool_calls(raw)
    if draft:
        return draft

    normalized, meta = normalize_message_content(
        raw.content if hasattr(raw, "content") else raw
    )
    draft = _extract_from_tool_blocks(meta)
    if draft:
        draft.raw_length = len(normalized)
        draft.meta.update(meta)
        return draft
    if not normalized and meta.get("thinking_snippets"):
        get_metrics_service().inc_contract_event("gateway_thinking_only")
        raise ValueError(
            "Model returned only thinking blocks; no text or tool output for structured writing. "
            "Configure provider to emit text/tool_use for writing purpose."
        )
    try:
        draft = _extract_from_text(normalized)
    except ValueError:
        get_metrics_service().inc_contract_event("gateway_parse_failed")
        raise
    draft.raw_length = len(normalized)
    draft.meta.update({k: v for k, v in meta.items() if k != "tool_blocks"})
    get_metrics_service().inc_contract_event(f"gateway_source_{draft.source}")
    return draft


def _resolve_writing_mode(
    *,
    user_payload: dict[str, Any],
    filename: str,
) -> str:
    mode = str(user_payload.get("writing_mode") or "").strip().lower()
    if mode in ("outline", "body"):
        return mode
    ctx = user_payload.get("writing_context") or {}
    action = str(ctx.get("action") or user_payload.get("writing_action") or "").lower()
    if action in ("write_outline", "rewrite_outline"):
        return "outline"
    fname = (filename or str(user_payload.get("filename") or "")).lower()
    if "outline" in fname or "大纲" in fname:
        return "outline"
    return "body"


def _writing_system_prompt(*, segment_index: int = 0, writing_mode: str = "body") -> str:
    base = (
        "You are a creative writing assistant for long-form Chinese fiction. "
        f"You MUST call the tool `{ARTIFACT_TOOL_NAME}` with the full plain text to save. "
        "Tool arguments must be a single JSON object with only the required `content` key—"
        "no reasoning, thinking, or analysis fields. "
        "Do not output thinking-only blocks. "
        "Obey writing_context.continuation_rules and writing_guidelines_excerpt when present. "
        "The content field must be Chinese text for the artifact, not meta commentary."
    )
    if writing_mode == "outline":
        base += (
            " OUTLINE mode: produce a full-book outline (title, characters, per-chapter plot beats "
            "as bullets or short lines). Do NOT write full chapter prose, dialogue scenes, or "
            "chapter footers （第N章完）. Ignore chapter_index for drafting a single chapter."
        )
    else:
        base += (
            " BODY mode: continue from novel_tail when present; follow outline_for_chapter; "
            "write only the chapter indicated by chapter_index; use UTF-8 TXT paragraph/dialogue "
            "layout and a chapter footer （第N章完）; never repeat earlier chapters or scenes."
        )
    if segment_index > 0:
        base += (
            "\nThis is a continuation segment: keep output concise, start the tool `content` "
            "string promptly, and do not prepend long meta or reasoning text in tool arguments."
        )
    return base


def is_stream_transport_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    if is_retryable_category(classify_llm_error(exc)):
        return True
    return any(
        token in message
        for token in (
            "incomplete chunked",
            "peer closed",
            "502",
            "bad gateway",
            "connection reset",
            "broken pipe",
        )
    )


def _draft_from_partial_stream(
    accumulated: str,
    parser: ArtifactArgsParser,
    merged: Any,
    *,
    stream_interrupted: bool,
    generation: GenerationRecord,
) -> ArtifactDraft | None:
    content = extract_streaming_content(accumulated, parser).strip()
    if not content and merged is not None:
        try:
            content = adapt_raw_response(merged).content.strip()
        except ValueError:
            content = extract_field_text(accumulated, "content").strip()
    if not content:
        return None
    meta = generation.to_meta()
    meta["stream_interrupted"] = stream_interrupted
    meta["stream_close"] = map_stream_close_status(
        has_content=True,
        stream_interrupted=stream_interrupted,
    )
    return ArtifactDraft(content=content, source="stream_partial", meta=meta)


_THINKING_ONLY_RETRY_SYSTEM_SUFFIX = (
    "\n\nCRITICAL: Your last response had only internal thinking blocks and no usable text. "
    f"Emit either a `{ARTIFACT_TOOL_NAME}` tool call with full Chinese prose in `content`, "
    'or exactly one JSON object {"content": "<full chapter or outline text>"}. '
    "Never reply with thinking-only blocks."
)


def is_thinking_only_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "thinking blocks" in msg or "thinking-only" in msg


def _invoke_artifact_sync(
    llm: Any,
    *,
    system: str,
    user: str,
    preferred: str,
) -> Any:
    """Non-stream invoke (tool-first with json_text fallback on tool errors)."""
    if preferred == "tool":
        try:
            return _invoke_with_tool(llm, system, user)
        except Exception as exc:
            message = str(exc).lower()
            if "timeout" in message or "rate" in message or "529" in message or "503" in message:
                raise RetryableError(str(exc)) from exc
            report_status_trace("writing", f"gateway: tool invoke failed ({exc}), fallback json")
            return _invoke_text(llm, system, user)
    return _invoke_text(llm, system, user)


def _adapt_or_retry_thinking_only_with_response(
    llm: Any,
    *,
    system: str,
    user: str,
    preferred: str,
) -> tuple[ArtifactDraft, Any]:
    """Parse model output; on thinking-only, one json_text retry (like planning stream fallback)."""
    try:
        response = _invoke_artifact_sync(llm, system=system, user=user, preferred=preferred)
        return adapt_raw_response(response), response
    except ValueError as exc:
        if not is_thinking_only_error(exc):
            raise
        get_metrics_service().inc_contract_event("gateway_thinking_retry")
        report_status_trace(
            "writing",
            "gateway: 模型仅返回 thinking，强制 JSON 正文重试…",
        )
        retry_system = system + _THINKING_ONLY_RETRY_SYSTEM_SUFFIX
        retry_user = (
            user
            + '\n\n"output_contract": "emit_visible_json_content_field_only_no_thinking_blocks"'
        )
        response = _invoke_text(llm, retry_system, retry_user)
        return adapt_raw_response(response), response


def _adapt_or_retry_thinking_only(
    llm: Any,
    *,
    system: str,
    user: str,
    preferred: str,
) -> ArtifactDraft:
    draft, _response = _adapt_or_retry_thinking_only_with_response(
        llm, system=system, user=user, preferred=preferred
    )
    return draft


@with_retry()
def _invoke_with_tool(llm: Any, system: str, user: str) -> Any:
    from langchain_core.messages import HumanMessage, SystemMessage

    tool_def = {
        "name": ARTIFACT_TOOL_NAME,
        "description": ARTIFACT_TOOL_SCHEMA["description"],
        "input_schema": ARTIFACT_TOOL_SCHEMA["input_schema"],
    }
    try:
        bound = llm.bind_tools([tool_def], tool_choice=ARTIFACT_TOOL_NAME)
    except TypeError:
        bound = llm.bind_tools([tool_def])

    return bound.invoke([SystemMessage(content=system), HumanMessage(content=user)])


@with_retry()
def _invoke_text(llm: Any, system: str, user: str) -> Any:
    from langchain_core.messages import HumanMessage, SystemMessage

    json_system = (
        system
        + "\nIf tools are unavailable, output ONE JSON object: {\"content\": \"<full text>\"}"
    )
    return llm.invoke([SystemMessage(content=json_system), HumanMessage(content=user)])


def _chunk_writing_buffer(chunk: Any) -> str:
    """Text/tool-args fragments that may contain partial JSON ``content``."""
    parts: list[str] = []
    for tcc in getattr(chunk, "tool_call_chunks", None) or []:
        if isinstance(tcc, dict):
            piece = tcc.get("args") or tcc.get("input") or ""
        else:
            piece = getattr(tcc, "args", None) or getattr(tcc, "input", None) or ""
        if piece:
            parts.append(str(piece))
    _thinking, text = extract_chunk_stream_parts(getattr(chunk, "content", ""))
    if text:
        parts.append(text)
    return "".join(parts)


def _stream_artifact_live(
    llm: Any,
    *,
    system: str,
    user: str,
    user_payload: dict[str, Any],
    filename: str,
    preferred: str,
    target_chars: int,
    trace_state: Any | None = None,
) -> ArtifactDraft:
    """Stream LLM output; push artifact body via writing_delta."""
    from langchain_core.messages import HumanMessage, SystemMessage

    task_id = str(user_payload.get("task_id") or "")
    segment_index = int(user_payload.get("chunk_index") or 0)
    generation = begin_generation(
        task_id=task_id,
        filename=filename,
        segment_index=segment_index,
        target_chars=target_chars,
    )

    report_artifact_stream_start(filename, target_chars=target_chars)
    report_status_trace("writing", f"gateway: 流式生成 {filename}…")

    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    stream_llm = llm
    if preferred == "tool":
        tool_def = {
            "name": ARTIFACT_TOOL_NAME,
            "description": ARTIFACT_TOOL_SCHEMA["description"],
            "input_schema": ARTIFACT_TOOL_SCHEMA["input_schema"],
        }
        try:
            stream_llm = llm.bind_tools([tool_def], tool_choice=ARTIFACT_TOOL_NAME)
        except TypeError:
            stream_llm = llm.bind_tools([tool_def])

    parser = ArtifactArgsParser()
    seen_content = 0
    buffer_trace_at = 0.0
    merged: Any = None
    last_usage_detail: dict[str, int] | None = None
    emit_thinking = thinking_stream_enabled()
    stream_started = time.monotonic()
    stream_interrupted = False
    aborted = False
    stream_session = None
    max_duration = int(getattr(settings, "ARTIFACT_STREAM_MAX_DURATION_SEC", 600))
    max_accumulated = int(getattr(settings, "ARTIFACT_STREAM_MAX_ACCUMULATED_CHARS", 120_000))

    try:
        for chunk in stream_llm.stream(messages):
            from app.services.llm_client import _extract_usage_detail

            chunk_usage = _extract_usage_detail(chunk)
            if chunk_usage:
                last_usage_detail = chunk_usage
            elapsed = time.monotonic() - stream_started
            if elapsed > max_duration:
                report_status_trace(
                    "writing",
                    f"流式生成已达 {max_duration}s 上限，尝试提交已缓冲正文…",
                )
                stream_interrupted = True
                get_metrics_service().inc_contract_event("artifact_stream_duration_cap")
                break
            if task_id:
                try:
                    from app.services.execution_control import (
                        CancelRequested,
                        PauseRequested,
                        check_for_control_signal,
                    )

                    step_epoch = None
                    if stream_session is not None:
                        active = (stream_session.state.get("interrupt_context") or {}).get(
                            "active_step"
                        )
                        if isinstance(active, dict):
                            step_epoch = active.get("foreground_epoch")
                    check_for_control_signal(
                        task_id,
                        phase="artifact_stream_chunk",
                        step_epoch=int(step_epoch) if step_epoch is not None else None,
                        raise_on_pause=True,
                        raise_on_cancel=True,
                        raise_on_epoch_stale=True,
                    )
                except (PauseRequested, CancelRequested):
                    report_status_trace("writing", "检测到任务控制停止，终止本次流式生成")
                    aborted = True
                    break
                except Exception:
                    pass
            merged = chunk if merged is None else merged + chunk
            piece = _chunk_writing_buffer(chunk)
            if not piece:
                continue
            if emit_thinking:
                thinking_part, _ = extract_chunk_stream_parts(getattr(chunk, "content", ""))
                if thinking_part:
                    report_thinking_delta(
                        node="writing",
                        phase="artifact_llm",
                        text=thinking_part,
                    )
            parser.feed(piece)
            if parser.args_len() > max_accumulated:
                report_status_trace(
                    "writing",
                    f"tool args 已超 {max_accumulated} 字符上限，终止读流并尝试提交已解析正文…",
                )
                stream_interrupted = True
                get_metrics_service().inc_contract_event("artifact_stream_args_cap")
                break
            accumulated = parser.accumulated
            prev_seen = seen_content
            seen_content = emit_artifact_content_deltas(
                accumulated,
                seen_content,
                filename=filename,
                parser=parser,
                task_id=task_id or None,
            )
            if prev_seen == 0 and seen_content > 0:
                generation.time_to_first_content_ms = int(elapsed * 1000)
                get_metrics_service().inc_contract_event("writing_content_first_byte")
                report_status_trace(
                    "writing",
                    f"{filename} 正文 content 已开始流式输出（手稿区将随后刷新）",
                )
            buffer_trace_at = maybe_report_artifact_buffer_trace(
                filename=filename,
                accumulated_len=parser.args_len(),
                content_seen_len=seen_content,
                last_report_at=buffer_trace_at,
                parser=parser,
            )
            if stream_session:
                live_content = extract_streaming_content(accumulated, parser)
                if live_content:
                    stream_session.on_content(live_content)
    except Exception as exc:
        from app.services.execution_control import CancelRequested, PauseRequested
        from app.services.foreground_execution import EpochStale

        if isinstance(exc, (EpochStale, PauseRequested, CancelRequested)):
            report_status_trace("writing", "检测到任务控制停止，终止本次流式生成")
            aborted = True
        elif is_stream_transport_error(exc):
            get_metrics_service().inc_contract_event("artifact_stream_transport_error")
            partial_draft = _draft_from_partial_stream(
                parser.accumulated,
                parser,
                merged,
                stream_interrupted=True,
                generation=generation,
            )
            if partial_draft and getattr(settings, "ARTIFACT_PARTIAL_ON_DISCONNECT", True):
                generation.finish(
                    status="partial",
                    args_bytes=parser.args_len(),
                    content_bytes=len(partial_draft.content),
                    error_class=classify_llm_error(exc).value,
                    extra={"stream_interrupted": True},
                )
                partial_draft.raw_length = parser.args_len()
                partial_draft.meta.update(generation.to_meta())
                report_status_trace(
                    "writing",
                    f"gateway: 流式连接中断，已保留 partial 正文 {len(partial_draft.content)} 字",
                )
                if partial_draft.content:
                    report_artifact_stream_done(filename, len(partial_draft.content))
                return partial_draft
            raise RetryableError(str(exc)) from exc
        raise

    accumulated = parser.accumulated
    if merged is not None:
        try:
            draft = adapt_raw_response(merged)
        except ValueError:
            content = extract_streaming_content(accumulated, parser)
            if content:
                draft = ArtifactDraft(content=content, source="stream_partial", meta={})
            else:
                if stream_interrupted or aborted:
                    partial_draft = _draft_from_partial_stream(
                        accumulated,
                        parser,
                        merged,
                        stream_interrupted=stream_interrupted,
                        generation=generation,
                    )
                    if partial_draft:
                        draft = partial_draft
                    else:
                        raise
                else:
                    raise
    else:
        content = extract_streaming_content(accumulated, parser) or accumulated
        draft = ArtifactDraft(
            content=content,
            source="stream_partial",
            meta={},
        )

    draft.raw_length = parser.args_len()
    if stream_session and draft.content:
        stream_session.finalize(draft.content)
    close = map_stream_close_status(
        has_content=bool(draft.content),
        stream_interrupted=stream_interrupted,
        aborted=aborted,
    )
    if close == "ok":
        gen_status = "committed"
    elif close == "partial":
        gen_status = "partial"
    elif close == "aborted":
        gen_status = "aborted"
    else:
        gen_status = "failed"
    generation.finish(
        status=gen_status,
        args_bytes=parser.args_len(),
        content_bytes=len(draft.content or ""),
        extra={"stream_close": close, "stream_interrupted": stream_interrupted},
    )
    draft.meta.update(generation.to_meta())
    if draft.content:
        report_artifact_stream_done(filename, len(draft.content))
    billed, usage_detail = (
        _usage_from_llm_response(merged) if merged is not None else (None, None)
    )
    if usage_detail is None and last_usage_detail:
        usage_detail = last_usage_detail
        billed = int(last_usage_detail["total_tokens"])
    _log_gateway_llm_interaction(
        purpose=str(user_payload.get("purpose") or "writing"),
        system=system,
        user=user,
        response_text=draft.content or "",
        user_payload=user_payload,
        status="ok" if draft.content else "empty",
        trace_state=trace_state,
        billed_tokens=billed,
        usage_detail=usage_detail,
        llm_response=merged,
    )
    return draft


def invoke_artifact_draft(
    *,
    purpose: str,
    task_desc: str,
    user_payload: dict[str, Any],
    filename: str = "artifact.txt",
    trace_state: Any | None = None,
) -> ArtifactDraft:
    """
    Generate artifact content via capability-driven adapter (tool preferred).
    """
    caps = build_model_capabilities()
    preferred = (caps.get("structured_output") or {}).get("preferred", "tool")
    segment_index = int(user_payload.get("chunk_index") or 0)
    fname = filename or str(user_payload.get("filename") or "artifact.txt")
    writing_mode = _resolve_writing_mode(user_payload=user_payload, filename=fname)
    system = _writing_system_prompt(
        segment_index=segment_index,
        writing_mode=writing_mode,
    )
    user = json.dumps(
        {"task": task_desc, **user_payload},
        ensure_ascii=False,
    )
    target_chars = int(user_payload.get("target_chars") or 0)

    llm = get_llm(purpose)
    if llm is None:
        from app.services.llm_client import _local_structured_response

        local = _local_structured_response("writing", user)
        content = str(local.get("content") or local.get("summary") or "local draft")
        draft = ArtifactDraft(content=content, source="local")
        _log_gateway_llm_interaction(
            purpose=purpose,
            system=system,
            user=user,
            response_text=content,
            user_payload=user_payload,
            status="ok",
            trace_state=trace_state,
        )
        return draft

    if artifact_stream_enabled():
        stream_retries = int(getattr(settings, "ARTIFACT_STREAM_MAX_RETRIES", 2))
        for attempt in range(stream_retries + 1):
            try:
                return _stream_artifact_live(
                    llm,
                    system=system,
                    user=user,
                    user_payload=user_payload,
                    filename=fname,
                    preferred=preferred,
                    target_chars=target_chars,
                    trace_state=trace_state,
                )
            except RetryableError as exc:
                if attempt >= stream_retries:
                    report_status_trace(
                        "writing",
                        f"gateway: 流式 transport 重试已用尽 ({exc})，回退单次调用…",
                    )
                    break
                report_status_trace(
                    "writing",
                    f"gateway: 流式 transport 失败，重试 {attempt + 1}/{stream_retries}…",
                )
            except Exception as exc:
                report_status_trace(
                    "writing",
                    f"gateway: 流式失败 ({exc})，回退单次调用…",
                )
                break

    report_status_trace("writing", "gateway: invoking model (tool-first)…")
    llm_response: Any = None
    try:
        draft, llm_response = _adapt_or_retry_thinking_only_with_response(
            llm,
            system=system,
            user=user,
            preferred=preferred,
        )
    except Exception as exc:
        _log_gateway_llm_interaction(
            purpose=purpose,
            system=system,
            user=user,
            response_text=str(exc),
            user_payload=user_payload,
            status="error",
            trace_state=trace_state,
        )
        raise
    if artifact_stream_enabled() and draft.content:
        emit_full_artifact_content_deltas(fname, draft.content, target_chars=target_chars)
    _log_gateway_llm_interaction(
        purpose=purpose,
        system=system,
        user=user,
        response_text=draft.content or "",
        user_payload=user_payload,
        status="ok" if draft.content else "empty",
        trace_state=trace_state,
        llm_response=llm_response,
    )
    return draft


def stream_artifact_draft(
    *,
    purpose: str,
    task_desc: str,
    user_payload: dict[str, Any],
    filename: str = "artifact.txt",
    trace_state: Any | None = None,
) -> ArtifactDraft:
    """Prefer live writing_delta stream; falls back to invoke path."""
    if artifact_stream_enabled() or trace_enabled():
        return invoke_artifact_draft(
            purpose=purpose,
            task_desc=task_desc,
            user_payload=user_payload,
            filename=filename,
            trace_state=trace_state,
        )
    return invoke_artifact_draft(
        purpose=purpose,
        task_desc=task_desc,
        user_payload=user_payload,
        filename=filename,
        trace_state=trace_state,
    )
