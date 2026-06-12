"""Mode contract → runtime routing helpers (qa / engineering; unified-core WP-6)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state

_WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})
_ARTIFACT_TOOLS = frozenset(
    {"read_text_artifact", "write_text_artifact", "append_text_artifact", "edit_text_artifact"}
)
_ENGINEERING_TOOLS = frozenset(
    {"mkdir_path", "write_file", "read_file", "verify_backend", "ls_path"}
)


def qa_mode_tools_resident() -> bool:
    from app.config.settings import settings

    return bool(getattr(settings, "QA_MODE_TOOLS_RESIDENT", False))


def target_mode_from_state(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("target_mode") or payload.get("current_mode") or "")


def execution_path_from_state(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("execution_path") or "")


def mode_blocks_writing(payload: dict[str, Any]) -> bool:
    """Contract-level writing gate (engineering always; qa depends on resident mode)."""
    mode = str(payload.get("target_mode") or "")
    if mode == "engineering_mode":
        return True
    if mode == "qa_mode" and qa_mode_tools_resident():
        intent = payload.get("writing_intent") or {}
        if intent.get("enabled"):
            return False
        profile = str(payload.get("thin_execution_profile") or "")
        if profile == "artifact_edit":
            return False
        tools = payload.get("selected_tools") or []
        if any(t in _WRITING_TOOL_NAMES or t == "edit_text_artifact" for t in tools):
            return False
        return True
    if mode == "manuscript_mode":
        return False
    if mode == "qa_mode":
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


def _strip_disallowed_tool_params(
    payload: dict[str, Any],
    kept_tools: list[str],
) -> dict[str, Any]:
    kept = set(kept_tools)
    if payload.get("tool_params"):
        payload["tool_params"] = {
            k: v for k, v in (payload.get("tool_params") or {}).items() if k in kept
        }
    return payload


def _writing_intent_from_kept(
    intent: dict[str, Any],
    kept: list[str],
    *,
    profile: str,
) -> dict[str, Any]:
    if profile == "artifact_edit":
        return {**intent, "enabled": True, "source": "artifact_edit"}
    has_write = any(
        t in _WRITING_TOOL_NAMES or t == "edit_text_artifact" for t in kept
    )
    enabled = bool(intent.get("enabled")) or has_write
    out = {
        **intent,
        "enabled": enabled,
        "source": intent.get("source") or "qa_mode_resident",
    }
    if not enabled:
        out.pop("blocked_by", None)
    return out


def apply_qa_mode_contract(
    state: AgentState,
    payload: dict[str, Any],
    audit: dict[str, Any],
    tools: list[str],
    intent: dict[str, Any],
    *,
    allowed: frozenset[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str], AgentState]:
    profile = str(payload.get("thin_execution_profile") or "")

    if qa_mode_tools_resident() and allowed:
        kept = strip_tools_for_mode(tools, allowed=allowed, forbid_engineering=True)
        audit["writing_blocked"] = False
        payload["writing_intent"] = _writing_intent_from_kept(intent, kept, profile=profile)
        payload = _strip_disallowed_tool_params(payload, kept)
        payload.pop("mission", None)
        state = merge_state(state, execution_mode="single")
        return payload, audit, kept, state

    if profile == "artifact_edit":
        kept = [t for t in tools if t in _ARTIFACT_TOOLS]
        audit["writing_blocked"] = False
        payload["writing_intent"] = {**intent, "enabled": True, "source": "artifact_edit"}
        state = merge_state(state, execution_mode="single")
        return payload, audit, kept, state

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


def apply_manuscript_mode_contract(
    state: AgentState,
    payload: dict[str, Any],
    audit: dict[str, Any],
    tools: list[str],
    intent: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any], list[str], AgentState]:
    kept = strip_tools_for_mode(tools, allowed=allowed, forbid_engineering=True) if allowed else list(tools)
    audit["writing_blocked"] = False
    payload["writing_intent"] = {
        **intent,
        "enabled": True,
        "source": intent.get("source") or "manuscript_mode",
    }
    task_id = str(state.get("task_id") or state.get("session_id") or "")
    if task_id:
        from app.services.writing_project import ensure_writing_project, writing_project_manifest_exists

        goal = str(payload.get("goal") or payload.get("query") or "")
        if not writing_project_manifest_exists(task_id):
            ensure_writing_project(task_id, goal=goal)
    payload["skip_retrieval"] = False
    payload = _strip_disallowed_tool_params(payload, kept)
    payload.pop("mission", None)
    state = merge_state(state, execution_mode="single")
    return payload, audit, kept, state
