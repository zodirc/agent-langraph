"""Flow (mission_act
  1. Tool: read_text_artifact(outline.txt)
  2. Model: plan old_text
  3. Tool: edit_text_artifact with those anchors

Outline steer patch — model picks anchor from read_text_artifact output, then edit tool applies it.
edit_plot on outline):
new_text from file content + user correction (structured)
No keyword matching on user phrasing. Does not rewrite the whole outline."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.manuscript_service import _is_outline_name


_OUTLINE_PATCH_SYSTEM = """You resolve a user correction against text returned by read_text_artifact.

The user already steered during a writing mission. You see the actual outline file content from the read tool.

Return ONE JSON object:
- "found": boolean — true if you can locate text to replace in the excerpt
- "old_text": string — exact substring from the excerpt to replace (must appear verbatim)
- "new_text": string — replacement after applying the user's correction
- "reason": short string if found is false

Rules:
- Patch only what the user changed (设定更正、名字、关系、细节). Do NOT rewrite the whole outline.
- old_text must be copied exactly from the provided outline_excerpt (minimal span).
- If the excerpt lacks context to anchor the edit, set found=false."""


def is_outline_filename(filename: str) -> bool:
    return _is_outline_name(str(filename or ""))


def plan_edit_from_read_content(
    outline_excerpt: str,
    steer_text: str,
) -> dict[str, Any]:
    """Model chooses anchor from tool-read content (not from a separate file read)."""
    from app.services.llm_client import invoke_structured

    text = (outline_excerpt or "").strip()
    steer = (steer_text or "").strip()
    if not text or not steer:
        return {"found": False, "reason": "missing read content or steer text"}

    user = json.dumps(
        {
            "user_correction": steer[-2000:],
            "outline_excerpt": text[:12000],
        },
        ensure_ascii=False,
    )
    try:
        raw = invoke_structured("planning", _OUTLINE_PATCH_SYSTEM, user)
    except (ValueError, RuntimeError, KeyError):
        return {"found": False, "reason": "outline patch planning failed"}

    if not isinstance(raw, dict):
        return {"found": False, "reason": "invalid planning output"}

    found = bool(raw.get("found"))
    old_text = str(raw.get("old_text") or "").strip()
    new_text = str(raw.get("new_text") or "").strip()
    if found and old_text and old_text in text:
        return {"found": True, "old_text": old_text, "new_text": new_text}
    return {
        "found": False,
        "reason": str(raw.get("reason") or "anchor not found in read excerpt"),
    }


def run_outline_edit_via_tools(state: AgentState, *, spec: dict[str, Any]) -> AgentState:
    """
    read_text_artifact → model anchor plan → edit_text_artifact.
    """
    from app.services.artifact_tools import handle_edit_text_artifact, handle_read_text_artifact
    from app.services.manuscript_service import resolve_manuscript

    task_id = state["task_id"]
    payload = dict(state.get("input_payload") or {})
    filename = str(spec.get("filename") or payload.get("outline_filename") or "outline.txt")
    steer_text = str(spec.get("steer_correction") or payload.get("goal") or "")

    read_out = handle_read_text_artifact(
        {"task_id": task_id, "filename": filename, "max_chars": 12000}
    )
    excerpt = str(read_out.get("content") or "")
    plan = plan_edit_from_read_content(excerpt, steer_text)
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

    edit_out = handle_edit_text_artifact(
        {
            "task_id": task_id,
            "filename": filename,
            "old_text": str(plan["old_text"]),
            "new_text": str(plan.get("new_text") or ""),
            "replace_all": bool(spec.get("replace_all", False)),
        }
    )
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    nbytes = int(edit_out.get("bytes") or 0)
    ms.outline_path = filename
    ms.outline_bytes = nbytes

    summary = (
        f"已按你的说明局部修改大纲 {filename}（替换 {len(plan['old_text'])} 字）。"
        "如需继续写正文，发送「继续」即可。"
    )
    return merge_state(
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
        status=TaskStatus.REASONED.value,
        current_node="mission_act",
    )
