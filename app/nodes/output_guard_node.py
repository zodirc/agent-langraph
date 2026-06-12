"""输出护栏

Output guard: skill validate + PII/optional LLM; fail → rejected.
After human_review resume: fixed edge human_review → output_guard."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.output_guard import GuardResult, evaluate_output
from app.services.state_store import get_state_store


def output_guard_node(state: AgentState) -> AgentState:
    """
    Scan reasoning summary before delivery (PII + optional LLM review).
    """
    from app.services.skill_metrics import record_skill_validation_failed
    from app.services.skill_output_validator import validate_skill_output

    skill_validation = validate_skill_output(state)
    if skill_validation.issues:
        record_skill_validation_failed(state, len(skill_validation.issues))

    if not getattr(settings, "OUTPUT_GUARD_ENABLED", True):
        return merge_state(
            state,
            output_guard_result={
                "passed": skill_validation.passed,
                "issues": skill_validation.issues,
                "source": "disabled",
                "skill_validation": skill_validation.to_dict(),
            },
            current_node="output_guard",
        )

    reasoning = state.get("reasoning_result") or {}
    text = str(reasoning.get("summary") or state.get("final_answer") or "")
    result = evaluate_output(text)
    if skill_validation.issues:
        record_skill_validation_failed(state, len(skill_validation.issues))
    if skill_validation.issues and skill_validation.strict:
        result = GuardResult(
            passed=False,
            issues=list(result.issues) + [f"skill:{i}" for i in skill_validation.issues],
            sanitized_summary=result.sanitized_summary,
            source=result.source,
        )

    faithfulness: dict[str, object] = {"skipped": True}
    grounding_result: dict[str, object] | None = None
    guard_warnings: list[str] = []
    from app.services.grounding_policy import (
        grounding_hits_for_state,
        should_run_grounding_check,
        substantive_tool_results,
    )

    if should_run_grounding_check(state):
        from app.services.evidence_pipeline import get_answer_mode, get_evidence_packets
        from app.services.grounding_check import (
            check_grounding,
            check_tool_observation_grounding,
            grounding_to_faithfulness,
        )

        grounding_mode, grounding_hits = grounding_hits_for_state(state)
        evidence_packets = (
            []
            if grounding_mode == "tool_observation"
            else get_evidence_packets(state)
        )
        answer_mode = get_answer_mode(state)
        if grounding_mode == "tool_observation":
            grounding = check_tool_observation_grounding(
                text,
                hits=grounding_hits,
                answer_mode=answer_mode,
            )
            if not grounding_hits and substantive_tool_results(state):
                grounding = grounding.model_copy(
                    update={
                        "grounded": True,
                        "score": 1.0,
                        "unsupported_claims": [],
                        "failure_tags": [],
                    }
                )
            faithfulness = grounding_to_faithfulness(grounding)
            faithfulness["evidence_source"] = grounding_mode
            grounding_result = faithfulness
        else:
            grounding = check_grounding(
                text,
                hits=grounding_hits,
                packets=evidence_packets,
                answer_mode=answer_mode,
            )
            grounding_result = grounding.model_dump()
            grounding_result["evidence_source"] = grounding_mode
            from app.services.evidence_hierarchy import collect_unified_evidence
            from app.services.insufficient_evidence import (
                apply_insufficient_to_guard,
                build_insufficient_response,
            )

            from app.runtime.evidence_models import EvidenceConflict

            insufficient = build_insufficient_response(
                mode=answer_mode,
                packets=evidence_packets or collect_unified_evidence(state),
                conflicts=[
                    EvidenceConflict.model_validate(c)
                    for c in (state.get("evidence_conflicts") or [])
                    if isinstance(c, dict)
                ],
                failure_tags=grounding.failure_tags
                + list((state.get("retrieval_trace") or {}).get("failure_tags") or []),
            )
            faithfulness = apply_insufficient_to_guard(
                grounding_to_faithfulness(grounding), insufficient
            )
            faithfulness["evidence_source"] = grounding_mode
            grounding_result = faithfulness
        from app.services.failure_attribution import attribute_failures, dashboard_rows
        attribution = attribute_failures(
            retrieval_trace=state.get("retrieval_trace") if isinstance(state.get("retrieval_trace"), dict) else None,
            grounding_result=grounding_result,
        )
        grounding_result["failure_attribution"] = attribution
        grounding_result["attribution_rows"] = dashboard_rows(attribution)
        if not faithfulness.get("faithful"):
            from app.services.grounding_policy import (
                is_factual_answer_mode,
                side_effects_verified,
                writing_turn_skips_rag_grounding,
            )
            issue = "faithfulness:" + "; ".join(
                (faithfulness.get("unsupported_claims") or [])[:3]
            )
            fake_citations = faithfulness.get("fake_citations") or []
            demote_to_warning = False

            # Hard REJECT only for fabricated citations or factual-QA without support.
            if not fake_citations and (
                (
                    grounding_mode == "tool_observation"
                    and side_effects_verified(state)
                )
                or writing_turn_skips_rag_grounding(state, answer_mode=answer_mode)
            ):
                demote_to_warning = True
            elif fake_citations or is_factual_answer_mode(answer_mode):
                demote_to_warning = False

            if demote_to_warning:
                faithfulness["demoted_to_warning"] = True
                grounding_result["demoted_to_warning"] = True
                guard_warnings.append(issue)
            else:
                result = GuardResult(
                    passed=False,
                    issues=list(result.issues) + [issue],
                    sanitized_summary=result.sanitized_summary,
                    source=result.source,
                )
    guard_payload = {**result.to_dict(), "faithfulness": faithfulness}
    if grounding_result is not None:
        guard_payload["grounding_check"] = grounding_result
    if guard_warnings:
        guard_payload["warnings"] = guard_warnings
    guard_payload["skill_validation"] = skill_validation.to_dict()
    updated = merge_state(
        state,
        output_guard_result=guard_payload,
        current_node="output_guard",
        audit_log=append_audit(
            state,
            "output_guard",
            "success" if result.passed else "blocked",
            {
                "passed": result.passed,
                "issues": result.issues,
                "warnings": guard_warnings,
                "source": result.source,
                "skill_validation": skill_validation.to_dict(),
            },
        ),
    )
    if skill_validation.issues and not skill_validation.strict:
        payload = dict(updated.get("input_payload") or {})
        payload["skill_validation_warnings"] = skill_validation.issues
        updated = merge_state(updated, input_payload=payload)
    elif skill_validation.issues and skill_validation.strict:
        contract = (state.get("skill_runtime_policy") or {}).get("resolved_output_contract") or {}
        if contract.get("retry_on_fail") and int(updated.get("reflection_count") or 0) < 2:
            payload = dict(updated.get("input_payload") or {})
            payload["skill_validation_feedback"] = skill_validation.issues
            updated = merge_state(
                updated,
                input_payload=payload,
                reflection_count=int(updated.get("reflection_count") or 0) + 1,
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
