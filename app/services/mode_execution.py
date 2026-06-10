"""Mode contract → runtime routing helpers (qa / engineering; unified-core WP-6)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state

_WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})
_ENGINEERING_TOOLS = frozenset(
    {"mkdir_path", "write_file", "read_file", "verify_backend", "ls_path"}
)


def target_mode_from_state(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("target_mode") or payload.get("current_mode") or "")


def execution_path_from_state(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("execution_path") or "")


def mode_blocks_writing(payload: dict[str, Any]) -> bool:
    """Contract-level writing gate (qa / engineering must not write artifacts)."""
    mode = str(payload.get("target_mode") or "")
    if mode in ("qa_mode", "engineering_mode"):
        return True
    blocked = (payload.get("writing_intent") or {}).get("blocked_by")
    if blocked in ("qa_mode", "engineering_mode", "mode_contract"):
        return True
    return False


def strip_tools_for_mode(
    tools: list[str],
    *,
    allowed: frozenset[str],
    forbid_engineering: bool = False,
) -> list[str]:
    out = list(tools)
    if allowed:
        out = [t for t in out if t in allowed]
    if forbid_engineering:
        out = [t for t in out if t not in _ENGINEERING_TOOLS]
    return out


def apply_qa_mode_contract(
    state: AgentState,
    payload: dict[str, Any],
    audit: dict[str, Any],
    tools: list[str],
    intent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str], AgentState]:
    intent = {
        **intent,
        "enabled": False,
        "blocked_by": "qa_mode",
        "source": "mode_contract",
    }
    audit["writing_blocked"] = True
    tools = strip_tools_for_mode(tools, allowed=frozenset(), forbid_engineering=True)
    tools = [t for t in tools if t not in _WRITING_TOOL_NAMES]
    if payload.get("tool_params"):
        payload["tool_params"] = {
            k: v
            for k, v in (payload.get("tool_params") or {}).items()
            if k not in _WRITING_TOOL_NAMES and k not in _ENGINEERING_TOOLS
        }
    payload["writing_intent"] = intent
    payload.pop("mission", None)
    state = merge_state(state, execution_mode="single")
    return payload, audit, tools, state
