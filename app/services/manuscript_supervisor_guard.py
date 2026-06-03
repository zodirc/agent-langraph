"""Block supervisor runtime for manuscript-bound writing missions (ADR-001)."""

from __future__ import annotations

from app.runtime.state import AgentState


def manuscript_mission_active(state: AgentState | dict) -> bool:
    mission = state.get("mission") or (state.get("input_payload") or {}).get("mission") or {}
    if str(mission.get("kind") or "").lower() != "writing":
        return False
    ms = state.get("manuscript") or {}
    if ms.get("body_path") or ms.get("outline_path"):
        return True
    payload = state.get("input_payload") or {}
    if payload.get("manuscript_paths"):
        return True
    if mission.get("total_target_chars") or (mission.get("success_criteria") or {}).get("target"):
        return True
    return bool(mission.get("orchestration", {}).get("enabled"))


def reject_supervisor_for_manuscript(state: AgentState) -> AgentState | None:
    """
    Return error state if supervisor was requested for a writing manuscript mission.
    Caller should use mission_oma instead.
    """
    if not manuscript_mission_active(state):
        return None
    from app.runtime.state import TaskStatus, append_audit, merge_state

    return merge_state(
        state,
        status=TaskStatus.FAILED.value,
        errors=list(state.get("errors") or [])
        + [
            "supervisor mode is not allowed for manuscript writing missions; "
            "use execution_mode=mission_oma"
        ],
        audit_log=append_audit(
            state,
            "supervisor_guard",
            "rejected",
            {"reason": "manuscript_bound_writing"},
        ),
    )
