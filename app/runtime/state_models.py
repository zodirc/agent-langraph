"""Pydantic models for nested AgentState fields (v0.10 — TypedDict compatible).

unified-core WP-6: mission/progress/step_decision models removed; legacy
top-level containers are still folded away for old snapshots.
"""

from __future__ import annotations

from typing import Any

# Re-export evidence OS models for state boundaries (§7.1).
from app.runtime.evidence_models import (  # noqa: F401
    CandidateEvidence,
    EvidenceConflict,
    EvidencePacket,
    GroundingCheckResult,
    QueryObject,
    RetrievalDecision,
    RetrievalTrace,
)


def coerce_agent_state(state: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy top-level containers (old snapshots) into §2.2 field families."""
    return _strip_legacy_top_level_fields(dict(state))


_LEGACY_TOP_LEVEL_FIELDS = (
    "mission",
    "progress",
    "mission_step",
    "mission_control",
    "exploration",
    "subtasks",
    "worker_results",
    "react_loop",
    "step_decision",
)


def _strip_legacy_top_level_fields(out: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy containers into §2.2 field families, then remove top-level keys."""
    payload = dict(out.get("input_payload") or {})
    plan_graph = dict(out.get("plan_graph") or {"nodes": []})
    meta = dict(plan_graph.get("meta") or {})
    bg = dict(out.get("background_status") or {})

    if isinstance(out.get("mission"), dict):
        meta["mission"] = out["mission"]
    if isinstance(out.get("progress"), dict):
        bg["progress"] = out["progress"]
        meta["progress"] = out["progress"]
    for key in ("mission_step", "mission_control", "exploration", "subtasks", "worker_results", "react_loop", "step_decision"):
        if out.get(key) is not None:
            meta[key] = out[key]

    if meta:
        plan_graph["meta"] = meta
        out["plan_graph"] = plan_graph
    if payload != out.get("input_payload"):
        out["input_payload"] = payload
    if bg:
        out["background_status"] = bg

    for key in _LEGACY_TOP_LEVEL_FIELDS:
        out.pop(key, None)
    return out
