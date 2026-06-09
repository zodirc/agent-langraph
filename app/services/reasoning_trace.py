"""Stream detailed human-readable reasoning traces to SSE clients."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator, Optional

from app.config.settings import settings
from app.services.stream_progress import report_answer_delta, report_trace

_ARTIFACT_CODE_CONTENT_RE = re.compile(
    r'"artifacts"\s*:\s*\[\s*\{[^}]*?"kind"\s*:\s*"code"[^}]*?'
    r'"content"\s*:\s*"((?:[^"\\]|\\.)*)',
    re.DOTALL | re.IGNORECASE,
)

_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "summary": re.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "content": re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "artifact_code": _ARTIFACT_CODE_CONTENT_RE,
    "risk_level": re.compile(r'"risk_level"\s*:\s*"([A-Z]+)"', re.IGNORECASE),
    "confidence": re.compile(r'"confidence"\s*:\s*([\d.]+)'),
    "thinking": re.compile(r'"thinking"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
}

_PLAN_ARRAY_RE = re.compile(r'"plan"\s*:\s*\[(.*)', re.DOTALL)
_PLAN_STEP_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_TOOLS_RE = re.compile(r'"selected_tools"\s*:\s*\[(.*?)\]', re.DOTALL)
_TOOL_NAME_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')

_THINKING_MARKERS = ("thinking", "reasoning_content", "redacted")


def is_thinking_blob(text: str) -> bool:
    lower = text.strip().lower()
    return any(marker in lower for marker in _THINKING_MARKERS) and (
        lower.startswith("{") or lower.startswith("'")
    )


def trace_enabled() -> bool:
    return bool(settings.REASONING_TRACE_ENABLED)


def trace_verbose() -> bool:
    return trace_enabled() and bool(settings.REASONING_TRACE_VERBOSE)


def answer_stream_enabled() -> bool:
    return bool(settings.ANSWER_STREAM_ENABLED)


def thinking_stream_enabled() -> bool:
    return bool(getattr(settings, "THINKING_STREAM_ENABLED", True))


def _decode_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def extract_field_text(accumulated: str, field: str) -> str:
    pattern = _FIELD_PATTERNS.get(field)
    if not pattern:
        return ""
    match = pattern.search(accumulated)
    if not match:
        return ""
    return _decode_json_string(match.group(1))


def extract_artifact_code_content(accumulated: str) -> str:
    """First structured.artifacts[] code block content from partial reasoning JSON."""
    return extract_field_text(accumulated, "artifact_code")


def stream_artifact_code_enabled() -> bool:
    from app.services.code_artifact_pipeline import (
        load_code_artifact_config,
        stream_artifact_incremental_enabled,
    )

    if not answer_stream_enabled():
        return False
    code_cfg = load_code_artifact_config()
    if code_cfg.enabled:
        return stream_artifact_incremental_enabled()
    raw = getattr(settings, "DISPLAY_CONFIG", None)
    if isinstance(raw, dict):
        stream_cfg = raw.get("stream") or {}
        if isinstance(stream_cfg, dict) and "include_artifact_code" in stream_cfg:
            return bool(stream_cfg["include_artifact_code"])
    return True


def max_artifact_stream_chars() -> int:
    raw = getattr(settings, "DISPLAY_CONFIG", None)
    if isinstance(raw, dict):
        stream_cfg = raw.get("stream") or {}
        if isinstance(stream_cfg, dict) and stream_cfg.get("max_artifact_stream_chars") is not None:
            return int(stream_cfg["max_artifact_stream_chars"])
    return 32000


def extract_plan_steps(accumulated: str) -> list[str]:
    match = _PLAN_ARRAY_RE.search(accumulated)
    if not match:
        return []
    inner = match.group(1)
    steps: list[str] = []
    for raw in _PLAN_STEP_RE.findall(inner):
        step = _decode_json_string(raw).strip()
        if step and (not steps or steps[-1] != step):
            steps.append(step)
    return steps


def extract_selected_tools(accumulated: str) -> list[str]:
    match = _TOOLS_RE.search(accumulated)
    if not match:
        return []
    return [_decode_json_string(t) for t in _TOOL_NAME_RE.findall(match.group(1))]


def report_block(node: str, phase: str, text: str, *, field: str = "detail", level: str = "detail") -> None:
    if not trace_enabled():
        return
    body = (text or "").strip()
    if body:
        report_trace(node=node, phase=phase, text=body, field=field, level=level)


def report_status_trace(node: str, message: str) -> None:
    report_block(node, "status", message, field="status", level="status")


def report_boundary(node: str, event: str, detail: str = "") -> None:
    label = "▶ 进入" if event == "enter" else "■ 完成"
    line = f"{label} [{node}]"
    if detail:
        line = f"{line} — {detail}"
    report_block(node, event, line, field="boundary", level="boundary")


def emit_field_deltas(
    accumulated: str,
    field: str,
    seen_len: int,
    *,
    node: str,
    phase: str,
    max_total: Optional[int] = None,
    min_delta: int = 1,
) -> int:
    text = extract_field_text(accumulated, field)
    if len(text) <= seen_len:
        return seen_len
    cap = max_total if max_total is not None else settings.REASONING_TRACE_MAX_CHARS
    if seen_len >= cap:
        return seen_len
    delta = text[seen_len:]
    new_len = min(len(text), cap)
    allowed = new_len - seen_len
    if allowed < min_delta:
        return seen_len
    chunk = delta[:allowed]
    if field == "thinking" and not settings.REASONING_TRACE_SHOW_THINKING:
        return new_len
    if field in ("summary", "artifact_code") and answer_stream_enabled():
        if field == "artifact_code" and not stream_artifact_code_enabled():
            return new_len
        if field == "summary":
            from app.services.stream_output_guard import (
                is_stream_output_blocked,
                scan_summary_before_emit,
            )

            if is_stream_output_blocked():
                return seen_len
            if scan_summary_before_emit(text):
                return seen_len
        report_answer_delta(node=node, phase=phase, text=chunk, field=field)
        return new_len
    if not trace_enabled():
        return new_len
    report_trace(node=node, phase=phase, text=chunk, field=field, level="delta")
    return new_len


def _emit_plan_step_deltas(
    accumulated: str,
    seen_steps: int,
    *,
    node: str,
    phase: str,
) -> int:
    steps = extract_plan_steps(accumulated)
    for idx in range(seen_steps, len(steps)):
        report_trace(
            node=node,
            phase=phase,
            text=f"  步骤 {idx + 1}: {steps[idx]}",
            field="plan_step",
            level="delta",
        )
    tools = extract_selected_tools(accumulated)
    if tools and trace_verbose():
        report_block(node, phase, f"  工具（流式识别）: {', '.join(tools)}", field="tools", level="delta")
    return len(steps)


def _progress_interval() -> int:
    return int(settings.REASONING_TRACE_PROGRESS_INTERVAL)


def _stream_fields() -> list[str]:
    if settings.REASONING_TRACE_SHOW_THINKING:
        return ["summary", "content", "thinking", "plan"]
    return ["summary", "content"]


def stream_llm_trace(
    chunks: Iterator[str],
    *,
    node: str,
    phase: str,
    field: str = "summary",
    extra_fields: Optional[list[str]] = None,
) -> str:
    if not trace_enabled() and not answer_stream_enabled():
        return "".join(chunks)

    if answer_stream_enabled():
        from app.services.stream_output_guard import reset_stream_output_guard

        reset_stream_output_guard()

    accumulated = ""
    seen: dict[str, int] = {field: 0}
    seen_plan_steps = 0
    fields = [field] + [f for f in (extra_fields or []) if f != field]
    if stream_artifact_code_enabled() and "artifact_code" not in fields:
        fields.append("artifact_code")
    if trace_verbose() and "plan" in phase:
        fields.extend(["risk_level", "confidence"])
    # Thinking is streamed via provider content_block (SSE thinking_delta), not JSON keys.

    cap = settings.REASONING_TRACE_MAX_CHARS
    last_progress_at = 0
    min_delta = 40 if trace_verbose() else 120
    summary_min_delta = 1 if answer_stream_enabled() else min_delta

    for chunk in chunks:
        if not chunk:
            continue
        accumulated += chunk

        if trace_enabled() and field == "summary" and '"plan"' in accumulated:
            seen_plan_steps = _emit_plan_step_deltas(
                accumulated, seen_plan_steps, node=node, phase=phase
            )

        for fname in fields:
            if fname in ("plan",):
                continue
            prev = seen.get(fname, 0)
            field_min_delta = summary_min_delta if fname == "summary" else min_delta
            cap_for_field = (
                max_artifact_stream_chars() if fname == "artifact_code" else cap
            )
            seen[fname] = emit_field_deltas(
                accumulated,
                fname,
                prev,
                node=node,
                phase=phase,
                max_total=cap_for_field,
                min_delta=field_min_delta,
            )

        if (
            trace_enabled()
            and trace_verbose()
            and len(accumulated) - last_progress_at >= _progress_interval()
        ):
            report_trace(
                node=node,
                phase=f"{phase}_recv",
                text=f"… 已接收 {len(accumulated)} 字符（流式解析中）",
                field="progress",
                level="progress",
            )
            last_progress_at = len(accumulated)

    return accumulated


def report_mode_resolution_trace(state: dict[str, Any]) -> None:
    if not trace_enabled():
        return
    payload = state.get("input_payload") or {}
    resolution = payload.get("mode_resolution") or {}
    if not resolution:
        return
    lines = [
        "【模式路由】",
        f"intent_kind: {resolution.get('intent_kind')}",
        f"target_mode: {resolution.get('target_mode')}",
        f"switch: {resolution.get('mode_switch_action')} ({resolution.get('mode_switch_reason')})",
    ]
    contract = resolution.get("effective_mode_contract") or {}
    if contract:
        lines.append(
            f"contract: path={contract.get('execution_path')} "
            f"delivery={contract.get('delivery_primary')} "
            f"tools={contract.get('allowed_tools')}"
        )
    report_block(
        "planning",
        "mode_resolution",
        "\n".join(lines),
        field="mode_resolution",
        level="detail",
    )


def report_route_audit_trace(state: dict[str, Any]) -> None:
    if not trace_enabled():
        return
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if not audit:
        return
    lines = [
        "【路由审计】",
        f"推断种类: {audit.get('inferred_kind')} (confidence={audit.get('kind_confidence')})",
        f"计划路由: {audit.get('planned_route')} aligned={audit.get('aligned')}",
    ]
    delivery = payload.get("delivery_plan") or {}
    if delivery:
        lines.append(
            f"交付: primary={delivery.get('primary')} secondary={delivery.get('secondary')}"
        )
    for issue in audit.get("issues") or []:
        lines.append(f"  问题: {issue}")
    corrections = audit.get("corrections") or []
    if corrections:
        lines.append(f"修正: {', '.join(str(c) for c in corrections)}")
    report_block("planning", "route_audit", "\n".join(lines), field="route_audit", level="detail")


def report_effective_plan_trace(state: dict[str, Any]) -> None:
    """Plan/tools after route_audit corrections (what the graph will actually run)."""
    if not trace_enabled():
        return
    from app.services.route_audit.apply import writing_gate_allowed

    payload = state.get("input_payload") or {}
    from app.services.turn_kind import plan_steps_for_display

    plan = plan_steps_for_display(state) or list(state.get("plan") or [])
    tools = list(state.get("selected_tools") or [])
    trace_tools = list(tools)
    if writing_gate_allowed(state) and (payload.get("writing_intent") or {}).get("enabled"):
        trace_tools.append("writing_node")
    audit = payload.get("route_audit") or {}
    meta = {
        "生效": True,
        "writing_blocked": audit.get("writing_blocked"),
        "artifact_profile": payload.get("artifact_profile") or audit.get("artifact_profile"),
    }
    lines = ["【生效计划】"]
    if plan:
        for idx, step in enumerate(plan, 1):
            lines.append(f"  {idx}. {step}")
    else:
        lines.append("  (无步骤)")
    if trace_tools:
        lines.append(f"工具: {', '.join(trace_tools)}")
    else:
        lines.append("工具: (无)")
    for key, value in meta.items():
        if value is not None and value != "":
            lines.append(f"{key}: {value}")
    report_block("planning", "plan_effective", "\n".join(lines), field="plan_effective", level="detail")


def report_plan_trace(
    plan: list[str],
    tools: list[str],
    *,
    dropped: list[str] | None = None,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    if not trace_enabled():
        return
    lines = ["【计划已定】"]
    for idx, step in enumerate(plan, 1):
        lines.append(f"  {idx}. {step}")
    if tools:
        lines.append(f"工具: {', '.join(tools)}")
    if dropped:
        lines.append(f"过滤: {', '.join(dropped)}")
    if meta and trace_verbose():
        for key, value in meta.items():
            if value is not None and value != "":
                lines.append(f"{key}: {value}")
    report_block("planning", "plan", "\n".join(lines), field="plan", level="detail")


def report_planning_input(state: dict[str, Any]) -> None:
    if not trace_verbose():
        return
    payload = state.get("input_payload") or {}
    from app.services.mission_steer import planning_steer_replan_active

    goal = str(payload.get("goal") or payload.get("query") or "")
    if planning_steer_replan_active(payload, state):
        steer = str(payload.get("latest_steer_message") or "").strip()
        if steer:
            goal = (
                "[STEER_REPLAN: 用户中途纠偏，以此为准重新规划]\n" + steer
            )
    goal = goal[:500]
    lines = [
        "【规划输入】",
        f"目标: {goal or '(空)'}",
        f"会话轮次: {state.get('session_turn', '?')}",
        f"风险: {payload.get('risk_level', 'LOW')}",
    ]
    trace_ctx = state.get("trace_context") or {}
    composition = trace_ctx.get("last_context_composition")
    if composition:
        lines.append("上下文治理 (ContextEnvelope):")
        lines.append(f"  kept: {composition.get('kept_count', '?')}")
        lines.append(f"  dropped: {composition.get('dropped_count', '?')}")
        lines.append(f"  compressed: {composition.get('compressed_count', '?')}")
        by_bucket = composition.get("items_by_bucket") or {}
        if by_bucket:
            lines.append(f"  buckets: {', '.join(sorted(by_bucket.keys()))}")
    else:
        from app.services.prompt_context_gateway import (
            context_governance_enabled,
            build_prompt_composition_for_state,
        )

        if context_governance_enabled():
            comp = build_prompt_composition_for_state(state, purpose="planning")
            lines.append(f"对话历史(治理): kept={comp.get('kept_count', '?')}")
        else:
            from app.services.conversation_context import (
                conversation_history_for_llm,
                conversation_history_from_state,
            )

            history = conversation_history_for_llm(conversation_history_from_state(state))
            if history:
                lines.append(f"对话历史: {len(history)} 条")
                if trace_verbose() and history:
                    last = history[-1]
                    role = last.get("role", "?")
                    content = str(last.get("content", ""))[:160]
                    lines.append(f"  最近一条 [{role}]: {content}")
    report_block("planning", "input", "\n".join(lines), field="input")


def report_reasoning_context(state: dict[str, Any]) -> None:
    if not trace_verbose():
        return
    payload = state.get("input_payload") or {}
    lines = ["【推理上下文】"]
    plan = state.get("plan") or []
    if plan:
        lines.append(f"计划 {len(plan)} 步: " + " → ".join(plan[:4]))
        if len(plan) > 4:
            lines.append(f"  …共 {len(plan)} 步")
    knowledge = state.get("retrieved_knowledge") or []
    lines.append(f"知识库命中: {len(knowledge)} 条")
    for idx, doc in enumerate(knowledge[:3], 1):
        snippet = str(doc.get("content") or doc.get("text") or doc)[:120]
        lines.append(f"  知识{idx}: {snippet}")
    memory = state.get("memory_hits") or []
    lines.append(f"记忆命中: {len(memory)} 条")
    for idx, hit in enumerate(memory[:3], 1):
        lines.append(f"  记忆{idx}: {str(hit.get('summary') or hit)[:120]}")
    tools = state.get("tool_results") or []
    lines.append(f"工具结果: {len(tools)} 项")
    for item in tools[:6]:
        name = item.get("tool", "?")
        status = item.get("status", "?")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        extra = ""
        if result.get("path"):
            extra = f" → {result.get('path')}"
        elif result.get("bytes"):
            extra = f" ({result.get('bytes')} B)"
        elif result.get("appended_bytes"):
            extra = f" (+{result.get('appended_bytes')} B)"
        lines.append(f"  · {name} [{status}]{extra}")
    chapter = payload.get("chapter_outline") or payload.get("longform_mode")
    if chapter or payload.get("longform_mode"):
        lines.append(
            f"长文模式: turn={payload.get('chapter_index', '?')} "
            f"目标字数={payload.get('requested_total_chars', '?')}"
        )
    report_block("reasoning", "context", "\n".join(lines), field="context")


def report_retrieval_trace(state: dict[str, Any]) -> None:
    if not trace_enabled():
        return
    knowledge = state.get("retrieved_knowledge") or []
    memory = state.get("memory_hits") or []
    lines = [f"【检索】知识 {len(knowledge)} 条, 记忆 {len(memory)} 条"]
    if trace_verbose():
        for idx, doc in enumerate(knowledge[:5], 1):
            score = doc.get("score")
            snippet = str(doc.get("content") or doc.get("text") or doc)[:150]
            prefix = f"({score:.2f}) " if isinstance(score, (int, float)) else ""
            lines.append(f"  知识{idx}: {prefix}{snippet}")
        for idx, hit in enumerate(memory[:5], 1):
            lines.append(f"  记忆{idx}: {str(hit.get('summary') or hit)[:150]}")
    report_block("retrieval", "done", "\n".join(lines), field="retrieval")


def report_tool_trace(state: dict[str, Any]) -> None:
    if not trace_enabled():
        return
    tools = state.get("selected_tools") or []
    results = state.get("tool_results") or []
    lines = [f"【工具执行】计划 {len(tools)} 个, 完成 {len(results)} 次调用"]
    payload = state.get("input_payload") or {}
    chunks = payload.get("append_chunks") or []
    if chunks:
        lines.append(f"预生成 append 分段: {len(chunks)} 段")
    for item in results:
        name = item.get("tool", "?")
        status = item.get("status", "?")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        detail_parts: list[str] = []
        if result.get("path"):
            detail_parts.append(str(result["path"]))
        if result.get("bytes") is not None:
            detail_parts.append(f"{result['bytes']} B")
        if result.get("appended_bytes") is not None:
            detail_parts.append(f"+{result['appended_bytes']} B")
        if result.get("mode"):
            detail_parts.append(str(result["mode"]))
        if trace_verbose() and result.get("content"):
            preview = str(result["content"])[:100].replace("\n", " ")
            detail_parts.append(f"预览: {preview}…")
        detail = " | ".join(detail_parts) if detail_parts else str(result)[:80]
        lines.append(f"  ✓ {name} [{status}] {detail}")
    report_block("tool_execution", "done", "\n".join(lines), field="tools")


def report_policy_trace(state: dict[str, Any]) -> None:
    if not trace_enabled():
        return
    reasoning = state.get("reasoning_result") or {}
    lines = [
        "【策略检查】",
        f"决策: {state.get('policy_result', '?')}",
        f"置信度: {reasoning.get('confidence', '?')}",
        f"风险: {reasoning.get('risk_level', '?')}",
        f"需人工审核: {bool(state.get('review_required'))}",
    ]
    if not answer_stream_enabled():
        summary = str(reasoning.get("summary") or "")[:300]
        if summary:
            lines.append(f"摘要: {summary}")
    report_block("policy", "done", "\n".join(lines), field="policy")


def report_mission_snapshot_trace(state: dict[str, Any]) -> None:
    """Structured one-line mission snapshot for diagnostics (answer.log style)."""
    if not trace_enabled():
        return
    import json

    obs = state.get("observation") or state.get("turn_facts") or {}
    metrics = obs.get("progress_metrics") or (state.get("progress") or {}).get("metrics") or {}
    manuscript = obs.get("manuscript") or state.get("manuscript") or {}
    intent = obs.get("writing_intent") or (state.get("input_payload") or {}).get(
        "writing_intent"
    ) or {}
    snapshot = {
        "turn": obs.get("turn") or state.get("session_turn"),
        "mission_step": obs.get("mission_step") or state.get("mission_step"),
        "body_bytes": manuscript.get("body_bytes"),
        "outline_bytes": manuscript.get("outline_bytes"),
        "outline_path": manuscript.get("outline_path"),
        "written_chars": metrics.get("written_chars"),
        "progress_pct": metrics.get("progress_pct"),
        "chapter_cursor": manuscript.get("chapter_cursor"),
        "chapter_index": intent.get("chapter_index"),
        "last_chapter_index": manuscript.get("last_chapter_index"),
        "revision": manuscript.get("revision"),
        "writing_command": (state.get("input_payload") or {}).get("writing_command"),
        "action": intent.get("action"),
    }
    report_block(
        "mission",
        "snapshot",
        json.dumps(snapshot, ensure_ascii=False),
        field="mission_snapshot",
    )


def emit_final_artifact_fences(structured: dict[str, Any] | None) -> None:
    """Append fenced code blocks once at end (composed_at_end stream policy)."""
    if not answer_stream_enabled() or not isinstance(structured, dict):
        return
    from app.services.answer_compose import should_include_code_artifacts

    from app.services.answer_compose import should_emit_verify_failure_message

    if not should_include_code_artifacts(structured):
        if should_emit_verify_failure_message(structured):
            from app.services.answer_compose import verify_failure_user_message

            msg = verify_failure_user_message(structured)
            if msg:
                report_answer_delta(
                    node="reasoning",
                    phase="reasoning_artifacts",
                    text="\n\n" + msg,
                    field="artifact_finalize",
                )
        return
    from app.services.code_artifact_pipeline import load_code_artifact_config

    code_cfg = load_code_artifact_config()
    if code_cfg.enabled and code_cfg.stream_mode != "composed_at_end":
        if code_cfg.stream_mode == "artifact_incremental":
            return
    from app.services.answer_compose import normalize_code_content, preserve_code_whitespace

    preserve = preserve_code_whitespace()
    blocks: list[str] = []
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
        blocks.append(f"```{fence_lang}\n{content}\n```")
    if not blocks:
        return
    report_answer_delta(
        node="reasoning",
        phase="reasoning_artifacts",
        text="\n\n" + "\n\n".join(blocks),
        field="artifact_finalize",
    )


def report_reasoning_result_trace(state: dict[str, Any], *, source: str) -> None:
    if not trace_enabled():
        return
    reasoning = state.get("reasoning_result") or {}
    lines = [
        f"【推理结果】来源={source}",
        f"confidence={reasoning.get('confidence')} risk={reasoning.get('risk_level')}",
    ]
    if not answer_stream_enabled():
        summary = str(reasoning.get("summary") or "")
        if summary:
            cap = 800 if trace_verbose() else 400
            lines.append(f"summary: {summary[:cap]}")
    structured = reasoning.get("structured")
    if isinstance(structured, dict) and structured:
        if structured.get("code_artifact_repaired"):
            lines.append("code_artifact: repaired=true")
        if structured.get("code_artifact_repair_failed"):
            lines.append("code_artifact: repair_failed=true")
        if structured.get("code_verify_ok") is True:
            lines.append("code_verify: ok=true")
        if structured.get("code_verify_failed"):
            lines.append("code_verify: failed=true")
        if trace_verbose() and structured.get("code_verify_reports"):
            lines.append(f"code_verify_reports: {structured.get('code_verify_reports')}")
        if trace_verbose():
            keys = ", ".join(structured.keys())
            lines.append(f"structured 字段: {keys}")
    report_block("reasoning", "result", "\n".join(lines), field="result", level="detail")


_MISSION_QUIET_TRACE_NODES = frozenset(
    {
        "mission_init",
        "mission_decide",
        "mission_act",
        "mission_observe",
        "mission_eval",
    }
)


def trace_after_node(node_name: str, state: dict[str, Any]) -> None:
    """Emit post-node snapshot traces (called from graph_runner SSE loop)."""
    if not trace_enabled():
        return
    status = str(state.get("status", ""))
    if node_name in _MISSION_QUIET_TRACE_NODES and status not in (
        "FAILED",
        "WRITING_FAILED",
        "DEAD_LETTER",
    ):
        return
    report_boundary(node_name, "exit", status)

    if node_name == "retrieval":
        report_retrieval_trace(state)
    elif node_name == "tool_execution":
        report_tool_trace(state)
    elif node_name == "policy":
        report_policy_trace(state)
    elif node_name == "writing":
        report_mission_snapshot_trace(state)
    elif node_name == "mission_observe":
        report_mission_snapshot_trace(state)
    elif node_name == "reasoning":
        audit = state.get("audit_log") or []
        source = "unknown"
        if audit:
            last = audit[-1]
            if last.get("node") == "reasoning" and isinstance(last.get("detail"), dict):
                source = str(last["detail"].get("source", source))
        report_reasoning_result_trace(state, source=source)
    elif node_name == "planning":
        payload = state.get("input_payload") or {}
        extras: list[str] = []
        if payload.get("append_chunks"):
            extras.append(f"预生成分段={len(payload['append_chunks'])}")
        if payload.get("prefill_error"):
            extras.append(f"预填错误={payload['prefill_error']}")
        if extras:
            report_block("planning", "prefill", "【规划收尾】 " + "; ".join(extras), field="prefill")
