"""
Runtime capability catalog — injected into planning/reasoning context so the model
knows which execution paths and tools exist (no goal-keyword routing).
"""

from __future__ import annotations

from typing import Any

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
        "execution_paths": [
            {
                "id": "single_turn",
                "when": "One-turn Q&A, small edits, or a single write/append in this turn.",
                "planning": (
                    'Set "writing_intent": {"enabled": true, "action": "write_outline|write_body|append_body", '
                    '"target_chars": <per-step only>}. Put prose in the Writing node, not in "summary" or tool_params.'
                ),
                "graph": "planning → (retrieval?) → tools? → writing? → reasoning → output",
            },
            {
                "id": "mission",
                "when": (
                    "User asks for sustained multi-step work in one session (e.g. many chapters, "
                    "total length >> one model reply, or autonomous step loop)."
                ),
                "planning": (
                    'Set "mission": {"kind":"writing","total_target_chars":N,'
                    '"step_policy":{"chars_per_step":M,"first_step":"outline","then":"append_body"},'
                    '"autonomous":true,'
                    '"budget":{"max_steps":<you decide, cap 500>}}. '
                    "Estimate max_steps from total/chars_per_step (+1 for outline); "
                    "runtime caps at 500 if omitted or too high. "
                    "Do NOT put the full book length in writing_intent.target_chars. "
                    "Per-step phase (write/review/polish/summary) is chosen by mission_decide LLM, not planning."
                ),
                "writing_phases": [
                    "write_outline",
                    "append_body",
                    "consistency_check",
                    "review_chapter",
                    "polish_chapter",
                    "chapter_summary",
                    "arc_checkpoint",
                ],
                "batch_unit_quality": (
                    "Steer-driven multi-chapter quality pass: work_plan_patch with "
                    "review_chapter (+ polish_chapter depends_on) per written chapter; "
                    "forbid append_body until batch queue completes."
                ),
                "graph": "planning ends → mission loop (decide → act → observe → eval) → output",
            },
        ],
        "writing": {
            "mechanism": "writing_intent (single-turn) or mission.step_policy (long-horizon)",
            "file_tools": ["write_text_artifact", "append_text_artifact"],
            "note": (
                "Fiction/manuscript prose uses the Writing node (novel.txt). "
                "Source code uses reasoning structured.artifacts — not write_body on novel.txt. "
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
            "mission_steps_hard_cap": int(getattr(settings, "MISSION_STEPS_HARD_CAP", 500)),
        },
        "constraints": [
            "No live web search; use retrieve_knowledge in plan only when local KB helps.",
            "Planning output must be ONE compact JSON object (no markdown fences, no prose chapters in plan).",
            "If mission is set, the runtime switches to the mission graph after planning in the same turn.",
        ],
    }
    return caps


def reasoning_instructions_for_state(state: dict[str, Any]) -> str:
    """Dynamic reasoning instructions from state shape (not goal keywords)."""
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    mission = state.get("mission") or payload.get("mission") or {}

    lines = [
        "Answer ONLY based on turn_facts for what already happened this turn.",
        "Do NOT claim future writes, append bytes, or tool runs absent from turn_facts.",
    ]
    if intent.get("enabled") or (isinstance(mission, dict) and mission):
        lines.append(
            'For creative/long-form goals: "summary" is a short status for the user '
            "(acknowledge the plan, state what ran, what you need next) — never paste "
            "chapter/outline/story text; that belongs in the Writing node via writing_intent or mission."
        )
    return " ".join(lines)
