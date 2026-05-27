from __future__ import annotations

import json

from app.config.prompts import REFLECTION_SYSTEM
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.llm_client import invoke_structured
from app.services.state_store import get_state_store


def _route_audit_issues(state: AgentState) -> list[str]:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("aligned") is not False:
        return []
    return [str(i) for i in (audit.get("issues") or [])[:5]]


def _rule_based_reflection(state: AgentState) -> dict[str, object]:
    """Critique when LLM is disabled — driven by fact_warnings and confidence."""
    reasoning = state.get("reasoning_result") or {}
    structured = reasoning.get("structured") or {}
    warnings = list(structured.get("fact_warnings") or [])
    confidence = float(reasoning.get("confidence", 1.0))
    issues: list[str] = []
    issues.extend(_route_audit_issues(state))
    if warnings:
        issues.extend(str(w) for w in warnings[:5])
    if confidence < 0.6:
        issues.append(f"low confidence ({confidence})")
    retry_reasoning = bool(issues) and not _route_audit_issues(state)
    retry_planning = bool(_route_audit_issues(state))
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
        payload = {
            "reasoning_result": reasoning,
            "turn_facts": state.get("turn_facts"),
            "goal": input_payload.get("goal"),
            "route_audit": input_payload.get("route_audit"),
            "writing_intent": input_payload.get("writing_intent"),
            "planned_route": (input_payload.get("route_audit") or {}).get("planned_route"),
        }
        user_json = json.dumps(payload, ensure_ascii=False)
        try:
            result = invoke_structured("reflection", REFLECTION_SYSTEM, user_json)
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
        if route_issues and not reflection.get("retry_planning"):
            reflection = {
                **reflection,
                "retry_planning": True,
                "retry_reasoning": False,
                "issues": list(dict.fromkeys(list(reflection.get("issues") or []) + route_issues)),
            }

        updated = merge_state(
            state,
            reflection_result=reflection,
            reflection_count=count,
            status=TaskStatus.REASONED.value,
            current_node="reflection",
            audit_log=append_audit(
                state,
                "reflection",
                "success",
                {
                    "retry_reasoning": reflection.get("retry_reasoning"),
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
