"""Planning-stage retry signals (retry_planning moved off post-answer reflection)."""

from __future__ import annotations

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.route_audit.config import load_route_audit_config


def _route_audit_issues(state: AgentState) -> list[str]:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("aligned") is not False:
        return []
    return [str(i) for i in (audit.get("issues") or [])[:5]]


def _turn_contract_issues(state: AgentState) -> list[str]:
    from app.services.turn_contract import validate_turn_contract_execution

    return validate_turn_contract_execution(state)


def planning_replan_needed(state: AgentState) -> bool:
    """True when post-planning route audit is still misaligned."""
    return bool(_route_audit_issues(state))


def execution_contract_replan_needed(state: AgentState) -> bool:
    """Post-tool contract drift that should trigger a planning revision."""
    return bool(_turn_contract_issues(state))


def apply_planning_replan_signal(
    state: AgentState,
    *,
    issues: list[str] | None = None,
) -> AgentState:
    """Flag incremental replan when route audit or execution contract requires it."""
    resolved = list(issues) if issues is not None else _route_audit_issues(state)
    if not resolved:
        return state
    payload = dict(state.get("input_payload") or {})
    if payload.get("route_audit_replan"):
        return state
    from app.services.route_audit.apply import build_replan_feedback

    audit = payload.get("route_audit") or {}
    payload["route_audit_replan"] = True
    payload["route_audit_replan_feedback"] = build_replan_feedback(
        {**audit, "issues": list(dict.fromkeys(list(audit.get("issues") or []) + resolved))}
    )
    revisions = int(state.get("planning_revision_count") or 0) + 1
    return merge_state(
        state,
        input_payload=payload,
        planning_revision_count=revisions,
    )


def can_planning_replan_again(state: AgentState) -> bool:
    cfg = load_route_audit_config()
    revisions = int(state.get("planning_revision_count") or 0)
    return revisions < cfg.max_planning_revisions


def degrade_to_writing_playbook_state(state: AgentState) -> AgentState | None:
    """When replan budget is exhausted, apply writing playbook instead of failing."""
    from app.services.writing_intent_classifier import classify_writing_operator
    from app.services.writing_playbook import apply_writing_playbook
    from app.services.writing_project import writing_project_manifest_exists

    payload = dict(state.get("input_payload") or {})
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    task_id = str(state["task_id"])
    operator = classify_writing_operator(goal, state) or str(payload.get("writing_operator") or "")
    if not operator or not writing_project_manifest_exists(task_id):
        return None
    actions, plan, patched = apply_writing_playbook(
        [],
        operator=operator,  # type: ignore[arg-type]
        goal=goal,
        task_id=task_id,
        state=state,
    )
    if not actions or not patched:
        return None
    from app.nodes.planning_node import _execution_transport_from_actions

    exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
    payload["writing_operator"] = operator
    payload["writing_intent"] = {"enabled": True, "source": "replan_budget_playbook"}
    payload["tool_params"] = {**payload.get("tool_params", {}), **tool_params}
    payload["tool_stages"] = stages
    payload["thin_execution_profile"] = "writing_playbook"
    return merge_state(
        state,
        input_payload=payload,
        plan=plan,
        planned_actions=[a.to_dict() for a in actions],
        selected_tools=exec_tools,
        skip_retrieval=False,
        status="PLANNED",
        current_node="planning",
        audit_log=append_audit(
            state,
            "planning",
            "replan_budget_playbook_degrade",
            {"writing_operator": operator},
        ),
    )


_FORCE_WRITE_FEEDBACK = (
    "Writing turn requires persisted artifact content. Context gathering is sufficient. "
    "Emit write_artifact (full rewrite) or append_artifact—do NOT plan read_artifact loops "
    "or edit_artifact without exact old_text/new_text. For polish/rewrite goals use "
    "write_artifact after at most one read."
)

_FORCE_EDIT_FEEDBACK = (
    "Character/name correction: reads are sufficient. Emit edit_artifact with edits[] "
    "(old_text, new_text, replace_all:true) for each affected file. Use with_line_numbers "
    "on the preceding read only once per file. Do NOT plan further read_artifact loops "
    "or write_artifact full rewrites."
)


def apply_force_write_signal(state: AgentState) -> AgentState:
    """Re-enter planning with a write/edit mandate without consuming replan budget."""
    from app.services.character_correction import (
        extract_name_replacements,
        is_character_correction_goal,
    )

    payload = dict(state.get("input_payload") or {})
    goal = str(payload.get("goal") or payload.get("query") or "")
    operator = str(payload.get("writing_operator") or "")
    if not operator:
        from app.services.writing_intent_classifier import classify_writing_operator

        inferred = classify_writing_operator(goal, state)
        if inferred:
            operator = inferred
            payload["writing_operator"] = inferred
    use_edit = (
        operator == "character" or is_character_correction_goal(goal)
    ) and bool(extract_name_replacements(goal))
    if use_edit:
        payload["force_edit_after_reads"] = True
        payload.pop("force_write_after_reads", None)
        payload["route_audit_replan_feedback"] = _FORCE_EDIT_FEEDBACK
    elif operator == "replot":
        payload.pop("force_write_after_reads", None)
        payload.pop("force_edit_after_reads", None)
        payload["writing_operator"] = "replot"
        payload["route_audit_replan_feedback"] = (
            "Outline replot turn: read outline then write_artifact back to the outline file. "
            "Do NOT answer-only or write body prose."
        )
    else:
        payload["force_write_after_reads"] = True
        payload.pop("force_edit_after_reads", None)
        payload["route_audit_replan_feedback"] = _FORCE_WRITE_FEEDBACK
    payload.pop("route_audit_replan", None)
    return merge_state(state, input_payload=payload)
