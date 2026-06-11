"""
Runtime capability catalog — injected into planning/reasoning context so the model
knows which execution paths and tools exist (no goal-keyword routing).
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.services.tool_registry import get_tool_registry


def build_runtime_capabilities(
    *,
    goal: str = "",
    domain: str = "",
    risk_level: str = "LOW",
    tool_top_k: Optional[int] = None,
    pack_tools: Optional[list[str]] = None,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Describe what the agent can do this run (tools, paths, limits)."""
    registry = get_tool_registry()
    top_k = tool_top_k if tool_top_k is not None else int(
        getattr(settings, "PLANNING_TOOL_TOP_K", 10)
    )

    if goal or domain or pack_tools:
        from app.services.tool_selection import format_tools_for_prompt, retrieve_relevant_tools

        relevant = retrieve_relevant_tools(
            goal,
            domain,
            risk_level,
            registry,
            top_k=top_k,
            pack_tools=pack_tools,
        )
        tools = format_tools_for_prompt(relevant)
        tool_selection_meta = {
            "mode": "retrieval_top_k",
            "top_k": top_k,
            "total_available": len(registry.list_tools()),
            "shown": len(tools),
        }
    else:
        tools = []
        for name in registry.list_tools():
            spec = registry.get(name)
            if spec is None:
                continue
            tools.append(
                {
                    "name": name,
                    "description": str(spec.description or "")[:240],
                    "risk_level": str(spec.risk_level or "LOW"),
                }
            )
        tool_selection_meta = {"mode": "full_registry", "shown": len(tools)}

    caps: dict[str, Any] = {
        "action_set": [
            "answer",
            "retrieve",
            "read_artifact",
            "write_artifact",
            "edit_artifact",
            "run_tool",
            "run_code",
        ],
        "execution_loop": (
            "planning emits actions → tool_execution runs them → converge gate decides "
            "replan / finalize → reasoning composes the answer."
        ),
        "writing": {
            "mechanism": "write_artifact / edit_artifact actions with explicit filename",
            "file_tools": ["write_text_artifact", "append_text_artifact", "edit_text_artifact"],
            "rename_tool": "move_path (run_tool: src/dst basenames under task artifact dir; equivalent to mv)",
            "delete_tool": "rm_path (run_tool: dry_run first → preview_token → commit delete)",
            "note": (
                "Long-form prose goes to artifact files via write/append actions; "
                "rename with move_path, not write+rm. "
                "route_audit corrects planner misroutes post-planning."
            ),
        },
        "route_audit": {
            "when": "After every planning step",
            "action": "Compare inferred task_kind vs planned_route; may block writing and force reasoning.",
        },
        "registered_tools": tools,
        "tool_selection": tool_selection_meta,
        "limits": {
            "artifact_chunk_chars": int(getattr(settings, "ARTIFACT_CHUNK_CHARS", 0)),
            "max_chars_per_turn": int(getattr(settings, "ARTIFACT_MAX_CHARS_PER_TURN", 0)),
            "model_max_tokens_planning": int(getattr(settings, "MODEL_MAX_TOKENS_PLANNING", 0)),
            "model_max_tokens_reasoning": int(getattr(settings, "MODEL_MAX_TOKENS_REASONING", 0)),
            "model_max_tokens_writing": int(getattr(settings, "MODEL_MAX_TOKENS_WRITING", 0)),
        },
        "constraints": [
            "No live web search; use retrieve_knowledge in plan only when local KB helps.",
            "Planning output must be ONE compact JSON object (no markdown fences, no prose chapters in plan).",
        ],
    }
    return caps


def reasoning_instructions_for_state(state: dict[str, Any]) -> str:
    """Dynamic reasoning instructions from state shape (not goal keywords)."""
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}

    lines = [
        "Answer ONLY based on turn_facts for what already happened this turn.",
        "Do NOT claim future writes, append bytes, or tool runs absent from turn_facts.",
    ]
    if intent.get("enabled"):
        lines.append(
            'For creative/long-form goals: "summary" is a short status for the user '
            "(acknowledge the plan, state what ran, what you need next) — never paste "
            "chapter/outline/story text; that content belongs in artifact files."
        )
    return " ".join(lines)
