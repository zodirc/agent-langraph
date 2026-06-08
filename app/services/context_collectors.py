"""Collect specialized context items (writing, code-agent, diagnostics)."""

from __future__ import annotations

import json
from typing import Any

from app.services.context_items import ContextItem, ContextPurpose, new_context_id


def _item(
    *,
    kind: str,
    content: str,
    priority: str = "medium",
    bucket: str | None = None,
    source: str = "workspace",
    role: str = "system",
    compressible: bool = True,
    droppable: bool = True,
    meta: dict[str, Any] | None = None,
) -> ContextItem:
    return ContextItem(
        id=new_context_id(kind[:4]),
        kind=kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        role=role,
        content=content.strip(),
        priority=priority,  # type: ignore[arg-type]
        compressible=compressible,
        droppable=droppable,
        bucket=bucket,  # type: ignore[arg-type]
        meta=meta or {},
    )


def collect_writing_context_items(
    state: dict[str, Any],
    *,
    purpose: ContextPurpose,
) -> list[ContextItem]:
    if purpose not in ("writing", "reviewing", "planning", "reasoning"):
        return []
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    items: list[ContextItem] = []
    task_id = str(state.get("task_id") or "")
    if not task_id:
        return items

    intent = payload.get("writing_intent") or {}
    manuscript = payload.get("manuscript") or state.get("manuscript") or {}
    from app.services.artifact_resolver import resolve_artifact_target

    body_name = resolve_artifact_target(
        state,
        action="read",
        target_hint="body",
        require_exists=False,
    ).filename
    outline_name = resolve_artifact_target(
        state,
        action="read",
        target_hint="outline",
        require_exists=False,
    ).filename
    chapter = intent.get("chapter_index")

    try:
        from app.services.manuscript_context import build_writing_context

        wctx = build_writing_context(
            task_id=task_id,
            state=state,
            body_filename=body_name,
            outline_filename=outline_name,
            chapter_index=int(chapter) if chapter is not None else None,
        )
    except Exception:
        wctx = {}

    if wctx.get("novel_tail"):
        items.append(
            _item(
                kind="file_slice",
                content=f"[novel_tail]\n{wctx['novel_tail']}",
                priority="high",
                bucket="file_context",
                droppable=purpose == "planning",
                meta={"file": body_name, "slice": "tail"},
            )
        )
    if wctx.get("outline_for_chapter"):
        items.append(
            _item(
                kind="file_slice",
                content=f"[outline_for_chapter]\n{wctx['outline_for_chapter']}",
                priority="high",
                bucket="file_context",
                meta={"file": outline_name, "slice": "chapter"},
            )
        )
    if wctx.get("writing_guidelines_excerpt"):
        items.append(
            _item(
                kind="knowledge",
                content=str(wctx["writing_guidelines_excerpt"])[:2500],
                priority="high",
                bucket="retrieved_knowledge",
                source="policy",
                meta={"source": "writing_guidelines"},
            )
        )
    for key in ("prev_chapter_summary", "prev_chapter_outcome", "current_chapter_goal"):
        if wctx.get(key):
            items.append(
                _item(
                    kind="working_memory",
                    content=f"[{key}] {wctx[key]}",
                    priority="medium",
                    bucket="working_memory",
                    source="state",
                )
            )

    excerpt = payload.get("previous_artifact_excerpt")
    if excerpt:
        items.append(
            _item(
                kind="file_slice",
                content=f"[previous_artifact_excerpt]\n{str(excerpt)[:3000]}",
                priority="medium",
                bucket="file_context",
                meta={"slice": "previous_artifact"},
            )
        )

    wi = payload.get("writing_intent") or {}
    if wi:
        items.append(
            _item(
                kind="working_memory",
                content=f"[writing_intent] {json.dumps(wi, ensure_ascii=False)[:2000]}",
                priority="high",
                bucket="working_memory",
                droppable=False,
                compressible=False,
                source="state",
            )
        )
    return items


def collect_code_agent_context_items(state: dict[str, Any]) -> list[ContextItem]:
    """IDE / code-agent slices: diffs, symbols, workspace summary, test failures."""
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    items: list[ContextItem] = []

    ctx = payload.get("code_context") or payload.get("workspace_context") or {}
    if isinstance(ctx, dict):
        if ctx.get("git_diff"):
            items.append(
                _item(
                    kind="git_diff",
                    content=str(ctx["git_diff"])[:6000],
                    priority="critical",
                    bucket="file_context",
                    droppable=False,
                    compressible=True,
                    meta={"path": ctx.get("path")},
                )
            )
        for sym in (ctx.get("symbol_slices") or ctx.get("symbols") or [])[:6]:
            if isinstance(sym, dict):
                text = sym.get("content") or sym.get("definition") or ""
            else:
                text = str(sym)
            if text:
                items.append(
                    _item(
                        kind="symbol_slice",
                        content=str(text)[:2500],
                        priority="high",
                        bucket="file_context",
                        meta={"symbol": sym.get("name") if isinstance(sym, dict) else None},
                    )
                )
        if ctx.get("workspace_summary"):
            items.append(
                _item(
                    kind="workspace_summary",
                    content=str(ctx["workspace_summary"])[:3000],
                    priority="medium",
                    bucket="file_context",
                )
            )

    for art in (payload.get("structured_output") or {}).get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        path = art.get("path") or art.get("filename")
        body = art.get("content") or art.get("snippet")
        if path and body:
            items.append(
                _item(
                    kind="file_slice",
                    content=f"[{path}]\n{str(body)[:4000]}",
                    priority="high",
                    bucket="file_context",
                    meta={"path": path},
                )
            )

    return items


def collect_diagnostic_context_items(state: dict[str, Any]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for err in (state.get("errors") or [])[:8]:
        text = str(err.get("message") or err)[:1500]
        if text:
            items.append(
                _item(
                    kind="diagnostic",
                    content=text,
                    priority="critical",
                    bucket="diagnostics",
                    droppable=False,
                    source="state",
                    meta={"node": err.get("node") if isinstance(err, dict) else None},
                )
            )

    payload = state.get("input_payload") or {}
    if isinstance(payload, dict):
        for fail in (payload.get("test_failures") or payload.get("diagnostics") or [])[:6]:
            items.append(
                _item(
                    kind="test_failure",
                    content=str(fail)[:2000],
                    priority="critical",
                    bucket="diagnostics",
                    droppable=False,
                )
            )
        term = payload.get("terminal_output") or payload.get("tool_stderr")
        if term:
            items.append(
                _item(
                    kind="terminal_output",
                    content=str(term)[:4000],
                    priority="high",
                    bucket="diagnostics",
                )
            )

    obs = state.get("observation")
    if isinstance(obs, dict) and obs:
        snippet = json.dumps(
            {
                "executed_actions": obs.get("executed_actions"),
                "tools_executed": obs.get("tools_executed"),
                "metrics": (obs.get("metrics") or {}) if isinstance(obs.get("metrics"), dict) else None,
            },
            ensure_ascii=False,
        )[:3500]
        items.append(
            _item(
                kind="tool_output",
                content=f"[mission_observation] {snippet}",
                priority="high",
                bucket="tool_observations",
                droppable=False,
                source="state",
            )
        )
    return items
