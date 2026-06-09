"""Single-turn intent freeze — one observation per (task_id, session_turn, user_input_hash)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.intent_observation import IntentObservationResult
from app.runtime.state import AgentState, merge_state
from app.services.artifact_tools import _normalize_edit_text


@dataclass
class IntentSnapshot:
    task_id: str
    session_turn: int
    user_input_hash: str
    intent_kind: str
    target_mode: str
    turn_kind_candidate: str | None
    is_revision: bool
    revision_intent: dict[str, Any] | None
    planning_required: bool
    snapshot_status: str
    created_at: str
    planning_required_source: str = "rule"
    source: str = "structural"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "session_turn": self.session_turn,
            "user_input_hash": self.user_input_hash,
            "intent_kind": self.intent_kind,
            "target_mode": self.target_mode,
            "is_revision": self.is_revision,
            "planning_required": self.planning_required,
            "planning_required_source": self.planning_required_source,
            "snapshot_status": self.snapshot_status,
            "created_at": self.created_at,
            "source": self.source,
        }
        if self.turn_kind_candidate:
            out["turn_kind_candidate"] = self.turn_kind_candidate
        if self.revision_intent:
            out["revision_intent"] = dict(self.revision_intent)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IntentSnapshot | None:
        if not isinstance(data, dict) or not data.get("task_id"):
            return None
        rev = data.get("revision_intent")
        return cls(
            task_id=str(data["task_id"]),
            session_turn=int(data.get("session_turn") or 0),
            user_input_hash=str(data.get("user_input_hash") or ""),
            intent_kind=str(data.get("intent_kind") or "qa"),
            target_mode=str(data.get("target_mode") or "qa_mode"),
            turn_kind_candidate=(
                str(data["turn_kind_candidate"]) if data.get("turn_kind_candidate") else None
            ),
            is_revision=bool(data.get("is_revision")),
            revision_intent=dict(rev) if isinstance(rev, dict) else None,
            planning_required=bool(data.get("planning_required", True)),
            snapshot_status=str(data.get("snapshot_status") or "frozen"),
            created_at=str(data.get("created_at") or ""),
            planning_required_source=str(data.get("planning_required_source") or "rule"),
            source=str(data.get("source") or "structural"),
        )


def hash_user_input(goal: str) -> str:
    normalized = _normalize_edit_text(goal)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _goal_from_state(state: AgentState) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("goal") or payload.get("query") or "").strip()


def _freeze_key(state: AgentState) -> tuple[str, int, str]:
    task_id = str(state.get("task_id") or "")
    session_turn = int(state.get("session_turn") or 0)
    user_hash = hash_user_input(_goal_from_state(state))
    return task_id, session_turn, user_hash


def current_intent_snapshot(state: AgentState | dict[str, Any]) -> IntentSnapshot | None:
    raw = state.get("intent_snapshot")
    if isinstance(raw, dict):
        return IntentSnapshot.from_dict(raw)
    payload = state.get("input_payload") or {}
    raw_payload = payload.get("intent_snapshot")
    if isinstance(raw_payload, dict):
        return IntentSnapshot.from_dict(raw_payload)
    return None


def invalidate_intent_snapshot(state: AgentState, reason: str) -> AgentState:
    snap = current_intent_snapshot(state)
    if snap is None:
        return state
    updated = IntentSnapshot(
        **{
            **snap.__dict__,
            "snapshot_status": "invalidated",
        }
    )
    payload = dict(state.get("input_payload") or {})
    payload["intent_recompute_reason"] = reason
    payload["intent_snapshot"] = updated.to_dict()
    return merge_state(
        state,
        intent_snapshot=updated.to_dict(),
        input_payload=payload,
    )


def snapshot_to_observation(snap: IntentSnapshot) -> IntentObservationResult:
    return IntentObservationResult(
        source=snap.source,
        intent_kind=snap.intent_kind,
        target_mode=snap.target_mode,
        session_relation="stay",
        turn_kind_candidate=snap.turn_kind_candidate,
        needs_planning=snap.planning_required,
        confidence=0.95,
        reasons=[f"intent_snapshot_hit=true", f"planning_source={snap.planning_required_source}"],
        is_revision=snap.is_revision,
        revision_intent=snap.revision_intent,
    )


def get_or_freeze_intent_snapshot(state: AgentState) -> IntentObservationResult | None:
    """Return cached observation when freeze key matches; None to proceed with fresh observe."""
    task_id, session_turn, user_hash = _freeze_key(state)
    if not task_id or not user_hash:
        return None
    snap = current_intent_snapshot(state)
    if snap is None:
        return None
    if snap.snapshot_status != "frozen":
        return None
    if (
        snap.task_id != task_id
        or snap.session_turn != session_turn
        or snap.user_input_hash != user_hash
    ):
        return None
    return snapshot_to_observation(snap)


def freeze_intent_snapshot(
    state: AgentState,
    result: IntentObservationResult,
    *,
    planning_required: bool,
    planning_required_source: str = "rule",
) -> AgentState:
    task_id, session_turn, user_hash = _freeze_key(state)
    snap = IntentSnapshot(
        task_id=task_id,
        session_turn=session_turn,
        user_input_hash=user_hash,
        intent_kind=result.intent_kind,
        target_mode=result.target_mode,
        turn_kind_candidate=result.turn_kind_candidate,
        is_revision=bool(result.is_revision),
        revision_intent=result.revision_intent,
        planning_required=planning_required,
        snapshot_status="frozen",
        created_at=datetime.now(timezone.utc).isoformat(),
        planning_required_source=planning_required_source,
        source=result.source,
    )
    payload = dict(state.get("input_payload") or {})
    payload["intent_snapshot"] = snap.to_dict()
    payload["intent_snapshot_hit"] = False
    payload["planning_required_source"] = planning_required_source
    return merge_state(state, intent_snapshot=snap.to_dict(), input_payload=payload)


def mark_snapshot_hit(state: AgentState) -> AgentState:
    payload = dict(state.get("input_payload") or {})
    payload["intent_snapshot_hit"] = True
    payload["intent_snapshot_source"] = "frozen"
    return merge_state(state, input_payload=payload)
