from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.output_guard import GuardResult, evaluate_output
from app.services.state_store import get_state_store


def output_guard_node(state: AgentState) -> AgentState:
    """
    Scan reasoning summary before delivery (PII + optional LLM review).
    """
    if not getattr(settings, "OUTPUT_GUARD_ENABLED", True):
        return merge_state(
            state,
            output_guard_result={"passed": True, "issues": [], "source": "disabled"},
            current_node="output_guard",
        )

    reasoning = state.get("reasoning_result") or {}
    text = str(reasoning.get("summary") or state.get("final_answer") or "")
    result = evaluate_output(text)

    faithfulness: dict[str, object] = {"skipped": True}
    if settings.RAG_FAITHFULNESS_CHECK_ENABLED:
        from app.services.rag_eval import check_faithfulness

        faithfulness = check_faithfulness(
            text,
            state.get("retrieved_knowledge") or [],
        )
        if not faithfulness.get("faithful"):
            result = GuardResult(
                passed=False,
                issues=list(result.issues)
                + [
                    "faithfulness:"
                    + "; ".join((faithfulness.get("unsupported_claims") or [])[:3])
                ],
                sanitized_summary=result.sanitized_summary,
                source=result.source,
            )

    updated = merge_state(
        state,
        output_guard_result={**result.to_dict(), "faithfulness": faithfulness},
        current_node="output_guard",
        audit_log=append_audit(
            state,
            "output_guard",
            "success" if result.passed else "blocked",
            {"passed": result.passed, "issues": result.issues, "source": result.source},
        ),
    )
    if not result.passed:
        updated = merge_state(
            updated,
            status=TaskStatus.REJECTED.value,
            policy_result="REJECT",
            errors=list(updated.get("errors", []))
            + [f"output_guard: {', '.join(result.issues)}"],
        )
    elif result.sanitized_summary:
        reasoning = dict(reasoning)
        reasoning["summary"] = result.sanitized_summary
        updated = merge_state(updated, reasoning_result=reasoning)

    get_state_store().save(updated)
    return updated
