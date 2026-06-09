"""Outline steer patch — read → anchor plan → edit (or rewrite when scope is global).

Flow (mission_act):
  1. Tool: read_text_artifact(outline.txt)
  2. Model: plan old_text / batch edits OR route to rewrite_outline
  3. Tool: edit_text_artifact with anchors
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.manuscript_service import _is_outline_name


_OUTLINE_PATCH_SYSTEM = """You resolve a user correction against text returned by read_text_artifact.

The user already steered during a writing mission. You see the actual outline file content from the read tool.

Return ONE JSON object:
- "found": boolean — true if you can locate text to replace in the excerpt
- "old_text": string — exact substring from the excerpt to replace (must appear verbatim), OR omit when using batch mode
- "new_text": string — replacement after applying the user's correction
- "edits": optional array of {"old_text","new_text","replace_all"} for batch character renames
- "start_line": optional int — line-based anchor when verbatim match is fragile
- "end_line": optional int
- "reason": short string if found is false

Rules:
- For localized fixes (设定更正、名字、关系、细节), patch minimal spans.
- For global character renames (multiple names), prefer "edits" batch with replace_all=true.
- old_text must be copied exactly from outline_excerpt when not using line numbers.
- If the excerpt lacks context to anchor the edit, set found=false."""


_OUTLINE_REWRITE_SYSTEM = """The user wants a substantial outline revision (e.g. replace fictional characters with original film cast).

Return ONE JSON object:
- "found": true
- "rewrite": true
- "reason": short summary of what to change"""


def is_outline_filename(filename: str) -> bool:
    return _is_outline_name(str(filename or ""))


def plan_edit_from_read_content(
    outline_excerpt: str,
    steer_text: str,
    *,
    global_rewrite: bool = False,
) -> dict[str, Any]:
    """Model chooses anchor from tool-read content (not from a separate file read)."""
    from app.services.llm_client import invoke_structured

    text = (outline_excerpt or "").strip()
    steer = (steer_text or "").strip()
    if not text or not steer:
        return {"found": False, "reason": "missing read content or steer text"}

    system = _OUTLINE_REWRITE_SYSTEM if global_rewrite else _OUTLINE_PATCH_SYSTEM
    user = json.dumps(
        {
            "user_correction": steer[-2000:],
            "outline_excerpt": text[:12000],
            "global_rewrite_hint": global_rewrite,
        },
        ensure_ascii=False,
    )
    try:
        raw = invoke_structured("planning", system, user)
    except (ValueError, RuntimeError, KeyError):
        return {"found": False, "reason": "outline patch planning failed"}

    if not isinstance(raw, dict):
        return {"found": False, "reason": "invalid planning output"}

    if global_rewrite and raw.get("rewrite"):
        return {"found": True, "rewrite": True, "reason": str(raw.get("reason") or "")}

    found = bool(raw.get("found"))
    edits = raw.get("edits")
    if found and isinstance(edits, list) and edits:
        valid = [
            e
            for e in edits
            if isinstance(e, dict) and str(e.get("old_text") or "").strip()
        ]
        if valid:
            return {"found": True, "edits": valid, "batch": True}

    old_text = str(raw.get("old_text") or "").strip()
    new_text = str(raw.get("new_text") or "").strip()
    start_line = raw.get("start_line")
    end_line = raw.get("end_line")
    if found and old_text and old_text in text:
        out: dict[str, Any] = {"found": True, "old_text": old_text, "new_text": new_text}
        if start_line is not None:
            out["start_line"] = int(start_line)
        if end_line is not None:
            out["end_line"] = int(end_line)
        return out
    if found and old_text:
        from app.services.artifact_tools import _find_fuzzy_span

        span = _find_fuzzy_span(text, old_text)
        if span is not None:
            rel_start, rel_end = span
            return {
                "found": True,
                "old_text": text[rel_start:rel_end],
                "new_text": new_text,
            }
    return {
        "found": False,
        "reason": str(raw.get("reason") or "anchor not found in read excerpt"),
    }


def run_outline_edit_via_tools(state: AgentState, *, spec: dict[str, Any]) -> AgentState:
    """
    read_text_artifact → model anchor plan → edit_text_artifact.
    """
    from app.services.artifact_tools import handle_edit_text_artifact, handle_read_text_artifact
    from app.services.edit_scope import should_rewrite_outline_not_patch
    from app.services.manuscript_service import resolve_manuscript

    task_id = state["task_id"]
    payload = dict(state.get("input_payload") or {})
    filename = str(spec.get("filename") or "")
    if not filename:
        from app.services.artifact_resolver import resolve_artifact_target

        filename = resolve_artifact_target(
            state,
            action="edit_plot",
            target_hint="outline",
            require_exists=True,
        ).filename
    steer_text = str(spec.get("steer_correction") or payload.get("goal") or "")

    from app.services.artifact_resolver import outline_exists

    read_out = handle_read_text_artifact(
        {
            "task_id": task_id,
            "filename": filename,
            "max_chars": 12000,
            "use_cache": True,
            "with_line_numbers": True,
        }
    )
    excerpt = str(read_out.get("raw_content") or read_out.get("content") or "")
    from app.services.edit_scope import analyze_edit_scope, classify_edit_action

    scope = analyze_edit_scope(steer_text, outline_excerpt=excerpt)
    if classify_edit_action(
        steer_text,
        outline_exists=outline_exists(state),
        outline_excerpt=excerpt,
    ) == "rewrite_outline":
        from app.nodes.writing_node import writing_node

        payload = dict(payload)
        payload["writing_intent"] = {
            "enabled": True,
            "action": "rewrite_outline",
            "reason": steer_text[:240],
            "source": "outline_global_rewrite",
            "edit_scope": scope.to_dict(),
        }
        state = merge_state(state, input_payload=payload)
        return writing_node(state)

    global_rewrite = should_rewrite_outline_not_patch(
        steer_text, outline_excerpt=excerpt, scope=scope
    )
    plan = plan_edit_from_read_content(excerpt, steer_text, global_rewrite=global_rewrite)
    if plan.get("rewrite"):
        from app.nodes.writing_node import writing_node

        payload = dict(payload)
        payload["writing_intent"] = {
            "enabled": True,
            "action": "rewrite_outline",
            "reason": str(plan.get("reason") or steer_text[:240]),
            "source": "outline_patch_rewrite",
        }
        state = merge_state(state, input_payload=payload)
        return writing_node(state)

    if not plan.get("found"):
        return merge_state(
            state,
            tool_results=[{"tool": "read_text_artifact", "status": "ok", "result": read_out}],
            reasoning_result={
                "summary": (
                    "已读取大纲，但未能定位要替换的原文。请补充章节名或贴出原句后再改。"
                    f"（{plan.get('reason', '')}）"
                ),
                "confidence": 0.75,
                "risk_level": "LOW",
                "structured": {"source": "outline_patch_failed"},
            },
            status=TaskStatus.REASONED.value,
            current_node="mission_act",
        )

    edit_params: dict[str, Any] = {
        "task_id": task_id,
        "filename": filename,
        "replace_all": bool(spec.get("replace_all", False)),
    }
    if plan.get("batch") and plan.get("edits"):
        edit_params["edits"] = plan["edits"]
    else:
        edit_params["old_text"] = str(plan["old_text"])
        edit_params["new_text"] = str(plan.get("new_text") or "")
        if plan.get("start_line") is not None:
            edit_params["start_line"] = plan["start_line"]
        if plan.get("end_line") is not None:
            edit_params["end_line"] = plan["end_line"]

    edit_out = handle_edit_text_artifact(edit_params)
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    nbytes = int(edit_out.get("bytes") or 0)
    ms.outline_path = filename
    ms.outline_bytes = nbytes

    replacements = int(edit_out.get("replacements") or 0)
    summary = (
        f"已按你的说明修改大纲 {filename}（{replacements} 处替换）。"
        "如需继续写正文，发送「继续」即可。"
    )
    from app.services.steer_planning_lifecycle import maybe_complete_steer_planning_after_execute
    from app.services.turn_guard import mark_turn_step_executed

    updated = merge_state(
        state,
        tool_results=[
            {"tool": "read_text_artifact", "status": "ok", "result": read_out},
            {"tool": "edit_text_artifact", "status": "ok", "result": edit_out},
        ],
        manuscript=ms.to_dict(),
        reasoning_result={
            "summary": summary,
            "confidence": 0.9,
            "risk_level": "LOW",
            "structured": {
                "source": "outline_tool_edit",
                "filename": filename,
            },
        },
        status=TaskStatus.TOOL_EXECUTED.value,
        current_node="mission_act",
    )
    updated = mark_turn_step_executed(updated)
    return maybe_complete_steer_planning_after_execute(updated)
