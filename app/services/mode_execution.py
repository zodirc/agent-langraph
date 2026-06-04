"""Mode contract → runtime routing helpers (all target_mode values)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state
from app.services.manuscript_service import WRITING_TOOL_NAMES
from app.services.mission_schema import should_use_mission_runtime

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
    """Contract-level writing gate (qa / engineering must not enter writing_node)."""
    mode = str(payload.get("target_mode") or "")
    if mode in ("qa_mode", "engineering_mode"):
        return True
    blocked = (payload.get("writing_intent") or {}).get("blocked_by")
    if blocked in ("qa_mode", "engineering_mode", "mode_contract"):
        return True
    return False


def should_route_mission_writing_mode(state: AgentState) -> bool:
    """manuscript_mode + mission contract → mission graph handoff after planning."""
    payload = state.get("input_payload") or {}
    if target_mode_from_state(state) != "manuscript_mode":
        return False
    if execution_path_from_state(state) != "mission_writing":
        return False
    return should_use_mission_runtime(payload, str(state.get("execution_mode") or ""))


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
    tools = [t for t in tools if t not in WRITING_TOOL_NAMES]
    payload["writing_intent"] = intent
    payload["disable_mission_auto"] = True
    payload.pop("mission", None)
    state = merge_state(state, mission=None, execution_mode="single")
    return payload, audit, tools, state


def apply_manuscript_mode_contract(
    state: AgentState,
    payload: dict[str, Any],
    audit: dict[str, Any],
    tools: list[str],
    intent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str], AgentState]:
    tools = strip_tools_for_mode(tools, allowed=frozenset(), forbid_engineering=True)
    audit["writing_blocked"] = False
    mission = payload.get("mission") or state.get("mission")
    if mission:
        payload["mission"] = mission
        mode = str(payload.get("execution_mode") or state.get("execution_mode") or "")
        if not mode or mode == "single":
            from app.config.settings import settings

            payload["execution_mode"] = str(
                getattr(settings, "MISSION_EXECUTION_MODE", "mission")
            )
        state = merge_state(
            state,
            mission=mission if isinstance(mission, dict) else None,
            execution_mode=payload.get("execution_mode"),
        )
    payload["writing_intent"] = intent
    return payload, audit, tools, state
