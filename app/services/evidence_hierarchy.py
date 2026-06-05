"""Unified evidence hierarchy across user, tool, memory, retrieval (§4.4.1 / §5.2)."""

from __future__ import annotations

from typing import Any

from app.runtime.evidence_models import EvidencePacket, ScoreBreakdown
from app.services.evidence_arbitration import authority_prior, source_priority

_LAYER_ORDER = (
    "user_input",
    "tool_result",
    "user_upload",
    "official_docs",
    "knowledge",
    "memory",
    "discussion",
)


def layer_rank(source_type: str) -> int:
    try:
        return len(_LAYER_ORDER) - _LAYER_ORDER.index(source_type)
    except ValueError:
        return 3


def user_evidence_from_state(state: dict[str, Any]) -> list[EvidencePacket]:
    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or payload.get("query") or payload.get("question") or "").strip()
    if not goal:
        return []
    return [
        EvidencePacket(
            packet_id="user_goal",
            snippet_text=goal[:2000],
            source_id="user_input",
            chunk_id="user_input",
            source_type="user_input",
            authority_level=authority_prior("user_input"),
            support_type="direct",
            score_breakdown=ScoreBreakdown(authority=1.0, total=1.0),
        )
    ]


def tool_evidence_from_state(state: dict[str, Any], *, limit: int = 5) -> list[EvidencePacket]:
    packets: list[EvidencePacket] = []
    for tool in (state.get("tool_results") or [])[:limit]:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("tool") or tool.get("name") or "tool")
        result = str(tool.get("result") or "")[:1500]
        if not result:
            continue
        packets.append(
            EvidencePacket(
                packet_id=f"tool_{name}",
                snippet_text=f"[{name}] {result}",
                source_id=f"tool:{name}",
                chunk_id=f"tool:{name}",
                source_type="tool_result",
                authority_level=authority_prior("tool_result"),
                support_type="direct",
                score_breakdown=ScoreBreakdown(authority=0.95, total=0.95),
            )
        )
    return packets


def memory_evidence_from_state(state: dict[str, Any], *, limit: int = 4) -> list[EvidencePacket]:
    packets: list[EvidencePacket] = []
    for hit in (state.get("memory_hits") or [])[:limit]:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("summary") or hit.get("content") or "")[:1500]
        if not text:
            continue
        packets.append(
            EvidencePacket(
                packet_id=f"mem_{hit.get('id', 'x')}",
                snippet_text=text,
                source_id=str(hit.get("id") or "memory"),
                chunk_id=str(hit.get("id") or "memory"),
                source_type="memory",
                authority_level=authority_prior("memory"),
                support_type="contextual",
                score_breakdown=ScoreBreakdown(authority=0.55, total=0.55),
            )
        )
    return packets


def retrieval_packets_from_state(state: dict[str, Any]) -> list[EvidencePacket]:
    raw = state.get("evidence_packets") or []
    return [EvidencePacket.model_validate(p) for p in raw if isinstance(p, dict)]


def collect_unified_evidence(state: dict[str, Any]) -> list[EvidencePacket]:
    """Merge all sources sorted by evidence hierarchy (highest first)."""
    all_packets = (
        user_evidence_from_state(state)
        + tool_evidence_from_state(state)
        + retrieval_packets_from_state(state)
        + memory_evidence_from_state(state)
    )
    return sorted(
        all_packets,
        key=lambda p: (layer_rank(p.source_type), -p.authority_level),
        reverse=True,
    )


def hierarchy_summary(packets: list[EvidencePacket]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in packets:
        counts[p.source_type] = counts.get(p.source_type, 0) + 1
    return counts
