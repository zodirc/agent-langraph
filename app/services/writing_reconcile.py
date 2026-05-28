"""Outline-body reconciliation: bridge segments and chapter patches."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.domain.writing_memory_models import AlignmentDecision, BridgeSpec, PatchInstruction
from app.runtime.state import AgentState
from app.services.artifact_content import generate_artifact_content
from app.services.artifact_tools import handle_append_text_artifact, handle_edit_text_artifact
from app.services.manuscript_context import extract_chapter_text, read_body_text
from app.services.manuscript_service import resolve_manuscript
from app.services.writing_memory import backup_chapter_text


def generate_bridge_segment(
    state: AgentState,
    *,
    spec: BridgeSpec,
    body_filename: str,
    outline_excerpt: str,
) -> str:
    default_chars = max(200, int(getattr(settings, "WRITING_BRIDGE_DEFAULT_CHARS", 600)))
    goal = (
        f"生成桥接段：{spec.bridge_goal}。"
        f"必须解决：{'; '.join(spec.must_resolve[:6])}。"
        "承接已有正文尾部，导向新大纲方向，不开启新章节标题。"
    )
    return generate_artifact_content(
        state=state,
        tool_name="append_text_artifact",
        filename=body_filename,
        goal=goal,
        target_chars=int(spec.target_chars or default_chars),
        chunk_index=0,
        chunk_total=1,
    )


def apply_bridge(
    state: AgentState,
    *,
    spec: BridgeSpec,
    decision: AlignmentDecision,
) -> tuple[AgentState, list[dict[str, Any]]]:
    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body_name = ms.body_path or str(settings.MANUSCRIPT_DEFAULT_BODY)
    payload = dict(state.get("input_payload") or {})
    outline_excerpt = str(payload.get("last_written_outline_excerpt") or "")[:4000]

    bridge_text = generate_bridge_segment(
        state,
        spec=spec,
        body_filename=body_name,
        outline_excerpt=outline_excerpt,
    )
    outcome = handle_append_text_artifact(
        {"task_id": task_id, "filename": body_name, "content": bridge_text}
    )
    ms = resolve_manuscript(task_id, ms.to_dict())
    tools = [
        {
            "tool": "writing_reconcile:bridge",
            "status": "ok",
            "result": {
                "bridge_chars": len(bridge_text),
                "decision": decision.to_dict(),
                "append": outcome,
            },
        }
    ]
    payload["outline_body_alignment_applied"] = "append_with_bridge"
    from app.runtime.state import merge_state

    return (
        merge_state(state, input_payload=payload, manuscript=ms.to_dict()),
        tools,
    )


def apply_chapter_patches(
    state: AgentState,
    *,
    instructions: list[PatchInstruction],
    decision: AlignmentDecision,
) -> tuple[AgentState, list[dict[str, Any]]]:
    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body_name = ms.body_path or str(settings.MANUSCRIPT_DEFAULT_BODY)
    body_text = read_body_text(task_id, body_name, state=state)
    tools: list[dict[str, Any]] = []
    backups: list[str] = []

    max_patches = max(1, int(getattr(settings, "WRITING_RECONCILE_MAX_PATCHES", 3)))
    min_patch_chars = max(
        40, int(getattr(settings, "WRITING_RECONCILE_MIN_PATCH_CHARS", 80))
    )
    for patch in instructions[:max_patches]:
        chapter_text = extract_chapter_text(body_text, patch.chapter_index)
        if not chapter_text:
            continue
        backup_path = backup_chapter_text(task_id, patch.chapter_index, chapter_text)
        backups.append(backup_path)
        goal = (
            f"按补丁修改第{patch.chapter_index}章（{patch.patch_type}）："
            f"{patch.instruction}。定位：{patch.target_section}"
        )
        patched = generate_artifact_content(
            state=state,
            tool_name="edit_text_artifact",
            filename=body_name,
            goal=goal,
            target_chars=max(len(chapter_text), 800),
        )
        if len(patched.strip()) < min_patch_chars:
            continue
        edit_out = handle_edit_text_artifact(
            {
                "task_id": task_id,
                "filename": body_name,
                "old_text": chapter_text,
                "new_text": patched.strip(),
                "replace_all": False,
            }
        )
        body_text = read_body_text(task_id, body_name, state=state)
        tools.append(
            {
                "tool": "writing_reconcile:patch",
                "status": "ok",
                "result": {
                    "chapter_index": patch.chapter_index,
                    "patch_type": patch.patch_type,
                    "backup": backup_path,
                    "edit": edit_out,
                },
            }
        )

    ms = resolve_manuscript(task_id, ms.to_dict())
    payload = dict(state.get("input_payload") or {})
    payload["outline_body_alignment_applied"] = "patch_recent_chapters"
    payload["patch_backups"] = backups
    from app.runtime.state import merge_state

    return (
        merge_state(state, input_payload=payload, manuscript=ms.to_dict()),
        tools,
    )
