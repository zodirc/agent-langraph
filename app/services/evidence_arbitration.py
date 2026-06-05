"""Evidence priority and conflict arbitration."""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import CandidateEvidence, EvidenceConflict, EvidencePacket

# Default authority hierarchy (higher = more trusted).
_DEFAULT_AUTHORITY: dict[str, float] = {
    "user_input": 1.0,
    "tool_result": 0.95,
    "user_upload": 0.90,
    "official_docs": 0.85,
    "knowledge": 0.70,
    "chroma": 0.70,
    "qdrant": 0.70,
    "keyword": 0.65,
    "rrf": 0.65,
    "memory": 0.55,
    "adjacency": 0.40,
    "discussion": 0.35,
}

_VALUE_CONFLICT_RE = re.compile(
    r"(?i)(version|port|timeout|enabled|disabled|true|false|\d+\.\d+)"
)


def authority_prior(source_type: str) -> float:
    mapping = getattr(settings, "RETRIEVAL_SOURCE_AUTHORITY_PRIOR", None) or {}
    if isinstance(mapping, dict) and source_type in mapping:
        return float(mapping[source_type])
    return _DEFAULT_AUTHORITY.get(source_type, 0.5)


def annotate_authority(candidates: list[CandidateEvidence]) -> list[CandidateEvidence]:
    for cand in candidates:
        cand.authority_level = authority_prior(cand.source_type)
        meta = cand.metadata or {}
        if meta.get("authority"):
            cand.authority_level = max(cand.authority_level, float(meta["authority"]))
    return candidates


def _extract_value_claims(text: str) -> dict[str, str]:
    claims: dict[str, str] = {}
    for line in text.splitlines()[:20]:
        line = line.strip()
        if not line or len(line) < 6:
            continue
        m = re.match(r"(?i)^([\w.-]+)\s*[:=]\s*(.+)$", line)
        if m:
            claims[m.group(1).lower()] = m.group(2).strip()[:120]
    return claims


def detect_cross_source_conflicts(
    retrieval: list[CandidateEvidence],
    user_packets: list[Any] | None = None,
    tool_packets: list[Any] | None = None,
) -> list[EvidenceConflict]:
    """Detect KB vs user/tool conflicts per §4.4.2."""
    conflicts: list[EvidenceConflict] = []
    user_packets = user_packets or []
    tool_packets = tool_packets or []

    def _claims(cand: CandidateEvidence) -> dict[str, str]:
        return _extract_value_claims(cand.content)

    for user in user_packets:
        uclaims = _extract_value_claims(getattr(user, "snippet_text", "") or "")
        for cand in retrieval:
            for key, uval in uclaims.items():
                kclaims = _claims(cand)
                if key in kclaims and kclaims[key] != uval:
                    winner, reason = arbitrate_between("user_input", cand.source_type)
                    conflicts.append(
                        EvidenceConflict(
                            field_key=key,
                            value_a=uval,
                            value_b=kclaims[key],
                            source_a_id="user_input",
                            source_b_id=cand.source_id,
                            source_a_type="user_input",
                            source_b_type=cand.source_type,
                            priority_winner="user_input" if winner == "a" else cand.source_id,
                            arbitration_reason=f"user_vs_kb:{reason}",
                        )
                    )
    for tool in tool_packets:
        tclaims = _extract_value_claims(getattr(tool, "snippet_text", "") or "")
        for cand in retrieval:
            for key, tval in tclaims.items():
                kclaims = _claims(cand)
                if key in kclaims and kclaims[key] != tval:
                    winner, reason = arbitrate_between("tool_result", cand.source_type)
                    conflicts.append(
                        EvidenceConflict(
                            field_key=key,
                            value_a=tval,
                            value_b=kclaims[key],
                            source_a_id=getattr(tool, "source_id", "tool"),
                            source_b_id=cand.source_id,
                            source_a_type="tool_result",
                            source_b_type=cand.source_type,
                            priority_winner=getattr(tool, "source_id", "tool") if winner == "a" else cand.source_id,
                            arbitration_reason=f"tool_vs_kb:{reason}",
                        )
                    )
    return conflicts


def detect_conflicts(
    candidates: list[CandidateEvidence],
) -> list[EvidenceConflict]:
    """Rule-based conflict detection on overlapping field keys."""
    conflicts: list[EvidenceConflict] = []
    by_key: dict[str, list[tuple[str, str, str, float]]] = {}

    for cand in candidates:
        claims = _extract_value_claims(cand.content)
        for key, value in claims.items():
            if not (_VALUE_CONFLICT_RE.search(value) or _VALUE_CONFLICT_RE.search(key)):
                continue
            by_key.setdefault(key, []).append(
                (cand.source_id, value, cand.source_type, cand.authority_level)
            )

    for key, entries in by_key.items():
        values = {e[1] for e in entries}
        if len(values) < 2:
            continue
        entries.sort(key=lambda e: e[3], reverse=True)
        winner = entries[0]
        loser = entries[1]
        conflicts.append(
            EvidenceConflict(
                field_key=key,
                value_a=winner[1],
                value_b=loser[1],
                source_a_id=winner[0],
                source_b_id=loser[0],
                source_a_type=winner[2],
                source_b_type=loser[2],
                priority_winner=winner[0],
                arbitration_reason=f"higher_authority:{winner[2]}>{loser[2]}",
                unresolved=winner[3] == loser[3],
            )
        )
    return conflicts


def apply_conflict_flags(
    packets: list[EvidencePacket],
    conflicts: list[EvidenceConflict],
) -> list[EvidencePacket]:
    conflict_sources = {c.source_b_id for c in conflicts if not c.unresolved}
    for pkt in packets:
        if pkt.source_id in conflict_sources:
            pkt.has_conflict = True
    return packets


def arbitration_summary(conflicts: list[EvidenceConflict]) -> dict[str, Any]:
    return {
        "conflict_count": len(conflicts),
        "unresolved": sum(1 for c in conflicts if c.unresolved),
        "fields": [c.field_key for c in conflicts[:5]],
    }


# Priority order per execution plan §4.4.1 (higher index = lower priority).
_SOURCE_PRIORITY: dict[str, int] = {
    "user_input": 7,
    "tool_result": 6,
    "user_upload": 5,
    "official_docs": 4,
    "knowledge": 3,
    "chroma": 3,
    "qdrant": 3,
    "memory": 2,
    "discussion": 1,
}


def source_priority(source_type: str) -> int:
    return _SOURCE_PRIORITY.get(source_type, 2)


def arbitrate_between(
    source_a_type: str,
    source_b_type: str,
    *,
    authority_a: float = 0.5,
    authority_b: float = 0.5,
) -> tuple[str, str]:
    """
    Return (winner, reason) per §4.4.2 rules.
    Tool > knowledge, official > discussion, higher authority wins ties.
    """
    pa = source_priority(source_a_type)
    pb = source_priority(source_b_type)
    if pa > pb:
        return "a", f"source_priority:{source_a_type}>{source_b_type}"
    if pb > pa:
        return "b", f"source_priority:{source_b_type}>{source_a_type}"
    if authority_a > authority_b:
        return "a", "authority_level"
    if authority_b > authority_a:
        return "b", "authority_level"
    return "", "unresolved"
