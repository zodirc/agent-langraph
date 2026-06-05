"""
Built-in agent prompts (server-side only, never returned to clients).
"""

from __future__ import annotations

from app.config.settings import settings
from app.config.prompt_templates import (
    MISSION_DECIDE_ROLE,
    PLANNING_ROLE,
    REACT_DELIBERATE_ROLE,
    REACT_INTERMEDIATE_REASON_ROLE,
    REASONING_COT_SUFFIX,
    REASONING_REACT_SUFFIX,
    REASONING_ROLE,
    REFLECTION_ROLE,
    resolve_system_prompt,
)
from app.services.tool_registry import get_tool_registry

# Core identity + capabilities — prepended to planning/reasoning system messages.
AGENT_CORE_PROMPT = """You are Agent LangGraph Runtime, a local task assistant (not a generic cloud chatbot).

Environment facts:
- Model name and limits come from get_runtime_info; do not invent model names.
- No live web search; use local knowledge retrieval only when needs_search is true.
- Registered tools can read/write text files under the task artifact directory (.txt/.md/.json etc.).

Behavior:
- Reply in the user's language (Chinese if the user writes Chinese).
- Be honest about limits; prefer tools over guessing for math, files, and runtime facts.
- Output must be valid JSON only (no markdown fences, no trailing commentary).
- Multi-turn: use conversation_history in the user JSON; the latest "goal" is the current message.
  Earlier turns are context — stay consistent with prior assistant answers in the same session.

Capabilities (see runtime_capabilities in user JSON for this run):
- Registered tools and execution_paths (single_turn vs mission) are listed there — choose paths in planning, not from memory.
- File prose is produced by the Writing node (writing_intent or mission.step_policy), not in planning/reasoning JSON.
- Long totals: use mission.total_target_chars + step_policy.chars_per_step; single-turn caps use writing_intent.target_chars only.
- Read manuscript / previous_artifact_excerpt in user JSON before continuing a manuscript.
"""


def agent_system_prompt(role_instructions: str) -> str:
    """Compose built-in core prompt + role-specific instructions."""
    extra = (settings.AGENT_PROMPT_EXTRA or "").strip()
    parts = [AGENT_CORE_PROMPT.strip(), role_instructions.strip()]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def resolve_reasoning_mode(state: dict) -> str:
    from app.config.prompt_templates import _resolve_reasoning_mode

    return _resolve_reasoning_mode(state)


def build_reasoning_system_prompt(
    mode: str | None = None,
    *,
    state: dict | None = None,
) -> str:
    return resolve_system_prompt(
        "reasoning",
        state=state,
        reasoning_mode=mode,
    )


PLANNING_SYSTEM = agent_system_prompt(PLANNING_ROLE)
REASONING_SYSTEM = build_reasoning_system_prompt("direct")
REFLECTION_SYSTEM = agent_system_prompt(REFLECTION_ROLE)
MISSION_DECIDE_SYSTEM = agent_system_prompt(MISSION_DECIDE_ROLE)
REACT_DELIBERATE_SYSTEM = agent_system_prompt(REACT_DELIBERATE_ROLE)
REACT_INTERMEDIATE_REASON_SYSTEM = agent_system_prompt(REACT_INTERMEDIATE_REASON_ROLE)


def build_planning_system_prompt(state: dict | None = None) -> str:
    tools = get_tool_registry().list_tools()
    tool_list = ", ".join(tools) if tools else "(none)"
    guide = (
        "Registry tools (selected_tools only — writing uses writing_intent / mission, not selected_tools):\n"
        f"  [{tool_list}]\n"
        "See runtime_capabilities.execution_paths in user JSON for when to set mission vs writing_intent.\n"
        "Keep planning JSON compact; plan steps are short labels, not story text or schema field names."
    )
    return resolve_system_prompt("planning", state=state, tool_guide=guide)
