"""Apply skill action_policy weights on top of domain action scores."""

from __future__ import annotations

from typing import Any, Optional

from app.services.action_resolver import CandidateAction


def skill_action_weights_from_state(state: dict[str, Any] | None) -> dict[str, float]:
    if not state:
        return {}
    policy = state.get("skill_runtime_policy") or {}
    if not isinstance(policy, dict):
        return {}
    raw = policy.get("resolved_action_weights") or {}
    if not isinstance(raw, dict):
        return {}
    weights: dict[str, float] = {}
    for key, val in raw.items():
        try:
            weights[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return weights


def apply_skill_action_weights(
    scored: list[CandidateAction],
    *,
    state: Optional[dict[str, Any]] = None,
    fallback_action: Optional[str] = None,
) -> list[CandidateAction]:
    """Boost or penalize candidate scores using skill_runtime_policy."""
    weights = skill_action_weights_from_state(state or {})
    snapshot = (state or {}).get("skill_snapshot") or {}
    action_policy = snapshot.get("action_policy") if isinstance(snapshot, dict) else {}
    fallback = fallback_action
    if not fallback and isinstance(action_policy, dict):
        fallback = action_policy.get("fallback_action")
    if not weights and not fallback:
        return scored

    adjusted: list[CandidateAction] = []
    for cand in scored:
        delta = weights.get(cand.action, 0.0)
        new_score = min(float(cand.score) + delta, 2.0)
        reason = cand.reason
        if delta:
            reason = f"{reason}; skill_weight={delta:+.2f}"
        adjusted.append(
            CandidateAction(
                action=cand.action,
                preconditions_met=cand.preconditions_met,
                score=new_score,
                reason=reason,
                metadata={**cand.metadata, "skill_weight_delta": delta},
            )
        )

    if fallback and adjusted:
        top = adjusted[0]
        if top.score < 0.55:
            for i, cand in enumerate(adjusted):
                if cand.action == fallback:
                    adjusted[i] = CandidateAction(
                        action=cand.action,
                        preconditions_met=cand.preconditions_met,
                        score=top.score + 0.15,
                        reason=f"{cand.reason}; skill_fallback_boost",
                        metadata=cand.metadata,
                    )
                    break

    policy = (state or {}).get("skill_runtime_policy") or {}
    if isinstance(policy, dict) and policy.get("resolved_plugin_hooks"):
        from app.services.skill_hooks import HOOK_TYPE_CANDIDATE_SCORE, apply_hook_stage

        patched = apply_hook_stage(
            policy,
            HOOK_TYPE_CANDIDATE_SCORE,
            context={"skill_id": (state or {}).get("skill_id")},
        )
        hook_weights = patched.get("resolved_action_weights") or {}
        if isinstance(hook_weights, dict) and hook_weights:
            reweighted: list[CandidateAction] = []
            for cand in adjusted:
                delta = float(hook_weights.get(cand.action, 0.0))
                reweighted.append(
                    CandidateAction(
                        action=cand.action,
                        preconditions_met=cand.preconditions_met,
                        score=min(float(cand.score) + delta, 2.0),
                        reason=f"{cand.reason}; hook_weight={delta:+.2f}",
                        metadata=cand.metadata,
                    )
                )
            adjusted = reweighted
    adjusted.sort(key=lambda c: (-c.score, c.action))
    return adjusted
