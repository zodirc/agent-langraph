"""Structured working memory, separate from transcript (ADR §5.4, §6.5)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.context_compressor import SemanticContextSummary, build_semantic_context_summary
from app.services.context_items import ContextItem, new_context_id


@dataclass
class WorkingMemory:
    goal: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    current_plan: list[str] = field(default_factory=list)
    executed_actions: list[str] = field(default_factory=list)
    tool_outcomes: list[str] = field(default_factory=list)
    pending_todos: list[str] = field(default_factory=list)
    open_risks: list[str] = field(default_factory=list)
    current_target_files: list[str] = field(default_factory=list)
    active_diagnostics: list[str] = field(default_factory=list)
    mission_snapshot: dict[str, Any] = field(default_factory=dict)
    version: str = "wm_v1"

    def to_text(self) -> str:
        parts = ["[Working memory]"]
        if self.goal:
            parts.append(f"Goal: {self.goal}")
        if self.hard_constraints:
            parts.append("Constraints: " + "; ".join(self.hard_constraints[:12]))
        if self.current_plan:
            parts.append("Plan: " + " → ".join(self.current_plan[:8]))
        if self.executed_actions:
            parts.append("Actions: " + "; ".join(self.executed_actions[:12]))
        if self.tool_outcomes:
            parts.append("Tools: " + "; ".join(self.tool_outcomes[:12]))
        if self.pending_todos:
            parts.append("Todos: " + "; ".join(self.pending_todos[:12]))
        if self.open_risks:
            parts.append("Risks: " + "; ".join(self.open_risks[:8]))
        if self.current_target_files:
            parts.append("Files: " + ", ".join(self.current_target_files[:8]))
        if self.active_diagnostics:
            parts.append("Diagnostics: " + "; ".join(self.active_diagnostics[:6]))
        return "\n".join(parts).strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_context_item(self) -> ContextItem:
        return ContextItem(
            id=new_context_id("wm"),
            kind="working_memory",
            source="state",
            role="system",
            content=self.to_text(),
            priority="high",
            compressible=True,
            droppable=False,
            bucket="working_memory",
            meta={"version": self.version},
        )


def _tool_outcomes_from_turn_facts(turn_facts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in turn_facts.get("tools_executed") or []:
        if isinstance(item, dict):
            name = item.get("tool") or item.get("name")
            status = item.get("status")
            if name:
                lines.append(f"{name}: {status or 'ok'}")
        else:
            lines.append(str(item))
    return lines[:20]


def working_memory_from_state(state: dict[str, Any] | None) -> WorkingMemory:
    if not state:
        return WorkingMemory()
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    turn_facts = state.get("turn_facts") or {}
    if not isinstance(turn_facts, dict):
        turn_facts = {}
    mission = state.get("mission") or payload.get("mission") or {}
    if not isinstance(mission, dict):
        mission = {}
    plan = state.get("plan") or []
    plan_steps = [str(p) for p in plan[:12]] if isinstance(plan, list) else []

    goal = ""
    if mission.get("objective"):
        goal = str(mission["objective"]).strip()
    if not goal:
        for key in ("goal", "query", "question"):
            if payload.get(key):
                goal = str(payload[key]).strip()
                break

    constraints: list[str] = []
    if payload.get("risk_level"):
        constraints.append(f"risk_level={payload['risk_level']}")

    executed = [str(a) for a in turn_facts.get("executed_actions") or []][:16]
    pending = [
        str(p)
        for p in (turn_facts.get("pending_todos") or turn_facts.get("open_todos") or [])
        if str(p) not in executed
    ][:12]
    if not pending and plan_steps:
        pending = [p for p in plan_steps if p not in executed][:8]

    wm = WorkingMemory(
        goal=goal,
        hard_constraints=constraints,
        current_plan=plan_steps,
        executed_actions=executed,
        tool_outcomes=_tool_outcomes_from_turn_facts(turn_facts),
        pending_todos=pending,
        open_risks=[str(r) for r in turn_facts.get("open_risks") or []][:8],
        mission_snapshot={
            "kind": mission.get("kind"),
            "status": mission.get("status"),
            "phase": mission.get("phase"),
        },
    )
    manuscript = payload.get("manuscript") or state.get("manuscript") or {}
    if isinstance(manuscript, dict):
        paths = []
        for key in ("outline_path", "body_path", "chapter_path"):
            if manuscript.get(key):
                paths.append(str(manuscript[key]))
        wm.current_target_files = paths[:8]
    return wm


def semantic_summary_item_from_state(
    state: dict[str, Any],
    history: list[dict[str, Any]],
) -> ContextItem | None:
    summary = build_semantic_context_summary(history, state=state)
    if summary is None:
        return None
    return ContextItem(
        id=new_context_id("sum"),
        kind="semantic_summary",
        source="session",
        role="system",
        content=summary.to_system_message().get("content", ""),
        priority="high",
        compressible=False,
        droppable=False,
        bucket="semantic_summary",
        meta={"summary": summary.to_dict() if hasattr(summary, "to_dict") else asdict(summary)},
    )


def working_memory_from_semantic_summary(summary: SemanticContextSummary) -> WorkingMemory:
    return WorkingMemory(
        goal=summary.goal,
        hard_constraints=list(summary.hard_constraints),
        current_plan=list(summary.pending_todos),
        executed_actions=list(summary.executed_facts),
        pending_todos=list(summary.pending_todos),
        open_risks=list(summary.open_risks),
    )


def working_memory_json_for_debug(wm: WorkingMemory) -> str:
    return json.dumps(wm.to_dict(), ensure_ascii=False, indent=0)[:4000]
