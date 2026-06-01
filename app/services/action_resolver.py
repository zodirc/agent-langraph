"""
Candidate action resolver — replaces hard-coded if/else step routing.

Flow: build_task_snapshot → domain pack candidates → score → select.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.runtime.state import AgentState


@dataclass
class TaskSnapshot:
    """Domain-agnostic task state snapshot."""

    has_bootstrap_artifact: bool
    has_main_artifact: bool
    main_artifact_bytes: int
    target_bytes: int
    step_index: int
    forced_intervention: str | None
    pending_review: bool
    failure_count: int
    policy_first_step: str = "outline"
    policy_then: str = "append_body"
    outline_max_chars: int = 500
    chars_per_step: int = 3000
    min_outline_chars: int = 80
    min_body_chars: int = 200
    last_chapter_index: int = 0
    revision: int = 0


@dataclass
class CandidateAction:
    action: str
    preconditions_met: bool
    score: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ActionDomainPack(Protocol):
    def candidate_actions(self, snapshot: TaskSnapshot) -> list[CandidateAction]: ...


def build_task_snapshot(state: AgentState, mission: dict[str, Any]) -> TaskSnapshot:
    """Extract objective state from runtime state + mission via domain pack."""
    from app.domain.packs.registry import resolve_mission_pack

    pack = resolve_mission_pack(
        mission_kind=str(mission.get("kind") or ""),
        task_type=str(state.get("task_type") or ""),
        payload=state.get("input_payload") or {},
    )
    return pack.build_task_snapshot(state, mission)


def score_candidates(
    candidates: list[CandidateAction],
    snapshot: TaskSnapshot,
) -> list[CandidateAction]:
    """Score and sort candidates; demote unmet preconditions."""
    scored: list[CandidateAction] = []
    for cand in candidates:
        score = float(cand.score)
        if not cand.preconditions_met:
            score *= 0.1
        if snapshot.failure_count > 0 and cand.action in ("reset_body", "write_body"):
            score += 0.05
        if snapshot.forced_intervention and cand.action == snapshot.forced_intervention:
            score += 1.0
        if (
            snapshot.target_bytes > 0
            and snapshot.has_main_artifact
            and snapshot.main_artifact_bytes >= snapshot.target_bytes
            and cand.action == "append_body"
        ):
            score *= 0.2
        scored.append(
            CandidateAction(
                action=cand.action,
                preconditions_met=cand.preconditions_met,
                score=min(score, 1.5),
                reason=cand.reason,
                metadata=dict(cand.metadata),
            )
        )
    scored.sort(key=lambda c: (-c.score, c.action))
    return scored


def list_candidate_actions(snapshot: TaskSnapshot, domain_pack: ActionDomainPack) -> list[CandidateAction]:
    return domain_pack.candidate_actions(snapshot)


def select_action(
    state: AgentState,
    mission: dict[str, Any],
    domain_pack: ActionDomainPack,
) -> CandidateAction:
    """Main entry: pick highest-scoring candidate action."""
    snapshot = build_task_snapshot(state, mission)
    candidates = list_candidate_actions(snapshot, domain_pack)
    if not candidates:
        fallback = "append_body" if str(mission.get("kind") or "") == "writing" else "continue"
        return CandidateAction(
            action=fallback,
            preconditions_met=True,
            score=0.5,
            reason="fallback: no candidates registered",
        )
    scored = score_candidates(candidates, snapshot)
    return scored[0]
