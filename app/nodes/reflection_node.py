"""反思节点
路由 route_after_reflection → planning | reasoning | policy (verdict-driven).

Reflection: critique reasoning before policy; may replan or retry reasoning."""

from __future__ import annotations

import json

from app.config.prompts import REFLECTION_SYSTEM
from app.domain.decision import apply_verdict_to_reflection, build_reflection_verdict
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.llm_client import invoke_structured
from app.services.state_store import get_state_store


def _route_audit_issues(state: AgentState) -> list[str]:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("aligned") is not False:
        return []
    return [str(i) for i in (audit.get("issues") or [])[:5]]


def _turn_contract_issues(state: AgentState) -> list[str]:
    from app.services.turn_contract import validate_turn_contract_execution

    return validate_turn_contract_execution(state)


def _event_log_issues(state: AgentState) -> list[str]:
    from app.services.turn_event_log import get_turn_event_log

    issues: list[str] = []
    for event in get_turn_event_log(state).failure_events():
        detail = event.detail or {}
        if event.event_type == "plan_rejected":
            for item in detail.get("issues") or []:
                issues.append(f"plan_rejected: {item}")
        elif event.event_type == "tool_blocked":
            for item in detail.get("issues") or []:
                issues.append(f"tool_blocked: {item}")
        else:
            issues.append(f"{event.event_type}: {event.subject}")
    return issues[:5]


def _writing_structured_reflection(state: AgentState) -> dict[str, object] | None:
    """Code-only reflection for writing turns — no reflection LLM."""
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    if not intent.get("enabled"):
        return None

    from app.services.writing_project import (
        chapter_char_count,
        expected_body_target,
        load_project,
    )

    task_id = str(state["task_id"])
    operator = str(payload.get("writing_operator") or "")
    issues: list[str] = []
    fixes: list[str] = []

    expected = expected_body_target(task_id, operator) if operator else None
    written_files: list[str] = []
    for item in state.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        if tool not in ("write_text_artifact", "append_text_artifact"):
            continue
        if str(item.get("status") or "ok") not in ("ok", "cached"):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        fname = str(result.get("filename") or "")
        if fname:
            written_files.append(fname)

    if expected and written_files and expected not in written_files:
        issues.append(f"正文写入文件不符：期望 {expected}，实际 {written_files}")
        fixes.append(f"改写到 {expected}")

    project = load_project(task_id)
    if project and expected and expected in written_files:
        count = chapter_char_count(task_id, expected)
        target = project.words_per_chapter
        if count < int(target * 0.7):
            issues.append(f"本章 {count} 字，低于目标 {target} 字的 70%")
            fixes.append("同轮续写本章末尾内容")

    outline = project.outline if project else ""
    if outline:
        for fname in written_files:
            if fname.replace("\\", "/") == outline.replace("\\", "/"):
                issues.append(f"大纲文件被意外修改: {fname}")

    return {
        "critique": "; ".join(issues) if issues else "writing checks passed",
        "retry_reasoning": False,
        "retry_planning": False,
        "issues": issues,
        "suggested_fixes": fixes,
        "source": "writing_structured",
    }


def _rule_based_reflection(state: AgentState) -> dict[str, object]:
    """Critique when LLM is disabled — driven by fact_warnings and confidence."""
    reasoning = state.get("reasoning_result") or {}
    structured = reasoning.get("structured") or {}
    warnings = list(structured.get("fact_warnings") or [])
    confidence = float(reasoning.get("confidence", 1.0))
    issues: list[str] = []
    issues.extend(_route_audit_issues(state))
    issues.extend(_turn_contract_issues(state))
    issues.extend(_event_log_issues(state))
    if warnings:
        issues.extend(str(w) for w in warnings[:5])
    if confidence < 0.6:
        issues.append(f"low confidence ({confidence})")
    retry_reasoning = bool(issues) and not _route_audit_issues(state)
    retry_planning = bool(_route_audit_issues(state) or _turn_contract_issues(state))
    return {
        "critique": "; ".join(issues) if issues else "no issues detected",
        "retry_reasoning": retry_reasoning,
        "retry_planning": retry_planning,
        "issues": issues,
        "source": "rules",
    }


def reflection_node(state: AgentState) -> AgentState:
    """
    Structured critique of reasoning_result (Ch4 Reflection).
    May route back to reasoning for one revision before policy.
    """
    count = int(state.get("reflection_count") or 0) + 1
    reasoning = state.get("reasoning_result") or {}
    try:
        input_payload = state.get("input_payload") or {}
        from app.services.turn_event_log import get_turn_event_log

        payload = {
            "reasoning_result": reasoning,
            "turn_facts": state.get("turn_facts"),
            "decision_facts": get_turn_event_log(state).decision_facts(),
            "goal": input_payload.get("goal"),
            "route_audit": input_payload.get("route_audit"),
            "writing_intent": input_payload.get("writing_intent"),
            "planned_route": (input_payload.get("route_audit") or {}).get("planned_route"),
        }
        user_json = json.dumps(payload, ensure_ascii=False)
        writing_reflection = _writing_structured_reflection(state)
        if writing_reflection is not None:
            reflection = writing_reflection
        else:
            try:
                result = invoke_structured(
                    "reflection",
                    REFLECTION_SYSTEM,
                    user_json,
                    trace_state=state,
                )
                reflection = {
                    "critique": str(result.get("critique", "")),
                    "retry_reasoning": bool(result.get("retry_reasoning", False)),
                    "retry_planning": bool(result.get("retry_planning", False)),
                    "issues": list(result.get("issues") or []),
                    "suggested_fixes": list(result.get("suggested_fixes") or []),
                    "source": "llm",
                }
            except (ValueError, RuntimeError):
                reflection = _rule_based_reflection(state)

        if not reflection.get("issues") and not reflection.get("critique"):
            reflection = _rule_based_reflection(state)

        route_issues = _route_audit_issues(state)
        contract_issues = _turn_contract_issues(state)
        replan_issues = route_issues + contract_issues
        if (
            replan_issues
            and not reflection.get("retry_planning")
            and reflection.get("source") != "writing_structured"
        ):
            reflection = {
                **reflection,
                "retry_planning": True,
                "retry_reasoning": False,
                "issues": list(dict.fromkeys(list(reflection.get("issues") or []) + replan_issues)),
            }

        verdict = build_reflection_verdict(state, reflection)
        reflection = apply_verdict_to_reflection(reflection, verdict)

        merge_updates: dict[str, object] = {
            "reflection_result": reflection,
            "reflection_count": count,
            "status": TaskStatus.REASONED.value,
            "current_node": "reflection",
        }
        if reflection.get("ask_human"):
            merge_updates["review_required"] = True

        updated = merge_state(
            state,
            **merge_updates,
            audit_log=append_audit(
                state,
                "reflection",
                "success",
                {
                    "retry_reasoning": reflection.get("retry_reasoning"),
                    "retry_planning": reflection.get("retry_planning"),
                    "recommended_action": (reflection.get("verdict") or {}).get(
                        "recommended_action"
                    ),
                    "round": count,
                    "source": reflection.get("source"),
                },
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            reflection_result={"critique": str(exc), "retry_reasoning": False, "source": "error"},
            reflection_count=count,
            errors=list(state.get("errors", [])) + [f"reflection: {exc}"],
            current_node="reflection",
            audit_log=append_audit(state, "reflection", "error", {"detail": str(exc)}),
        )
