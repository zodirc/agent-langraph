"""End-to-end evidence preservation metrics (P1-2)."""

from __future__ import annotations

from typing import Any

from app.services.context_items import ContextItem
from app.services.relevance_gate import relevance_score


def compute_evidence_fidelity(
    state: dict[str, Any] | None,
    items_kept: list[ContextItem],
    *,
    score_floor: float = 0.35,
) -> dict[str, Any]:
    """
    Fidelity = high-score retrieval packets that survived into the prompt envelope.
    """
    if not state:
        return {"rate": 1.0, "high_score_total": 0, "high_score_kept": 0}

    packets = state.get("evidence_packets") or []
    if not isinstance(packets, list) or not packets:
        return {"rate": 1.0, "high_score_total": 0, "high_score_kept": 0}

    high_ids: list[str] = []
    for pkt in packets:
        if not isinstance(pkt, dict):
            continue
        score = relevance_score(pkt)
        if score >= score_floor:
            pid = str(pkt.get("packet_id") or pkt.get("chunk_id") or "")
            if pid:
                high_ids.append(pid)

    if not high_ids:
        return {"rate": 1.0, "high_score_total": 0, "high_score_kept": 0}

    kept_ids = {
        str(i.meta.get("evidence_packet_id") or i.meta.get("doc_id") or "")
        for i in items_kept
        if i.kind == "knowledge"
    }
    kept_ids.discard("")
    high_kept = sum(1 for pid in high_ids if pid in kept_ids)
    rate = high_kept / max(len(high_ids), 1)
    return {
        "rate": round(rate, 4),
        "high_score_total": len(high_ids),
        "high_score_kept": high_kept,
    }
