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
- Registered tools are listed there — choose tools in planning, not from memory.
- Documents are written/edited through artifact actions (write_artifact / edit_artifact / read_artifact) executed in the tool loop.
- Read artifact_manifest / previous_artifact_excerpt in user JSON before continuing or editing an existing document.
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
        "Registry tools (for run_tool actions):\n"
        f"  [{tool_list}]\n"
        "Artifact read/write/edit and retrieval have dedicated action types; "
        "use run_tool only for other registry tools (calculator, get_runtime_info, …).\n"
        "When the user asks to modify/polish/revise an existing artifact and "
        "artifact_manifest in user JSON is non-empty, emit read_artifact then "
        "write_artifact (full revised text) or edit_artifact (exact old_text) "
        "with an explicit filename from artifact_manifest.\n"
        "Keep planning JSON compact; plan steps are short labels, not story text or schema field names."
    )
    return resolve_system_prompt("planning", state=state, tool_guide=guide)
