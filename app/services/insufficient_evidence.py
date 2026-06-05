"""Structured responses when evidence is insufficient (§4.5.4)."""

from __future__ import annotations

from typing import Any

from app.runtime.evidence_models import EvidenceConflict, EvidencePacket


def build_insufficient_response(
    *,
    mode: str = "best_effort_grounded",
    packets: list[EvidencePacket] | None = None,
    conflicts: list[EvidenceConflict] | None = None,
    failure_tags: list[str] | None = None,
) -> dict[str, Any]:
    """
    Return structured limited/refusal payload.

    Cases: no_evidence | partial_evidence | conflicting_evidence
    """
    packets = packets or []
    conflicts = conflicts or []
    tags = failure_tags or []

    if conflicts:
        points = [
            f"{c.field_key}: {c.value_a} vs {c.value_b} (prefer {c.priority_winner})"
            for c in conflicts[:3]
        ]
        return {
            "response_type": "conflicting_evidence",
            "message": "检测到证据冲突，无法给出单一确定结论。",
            "conflict_points": points,
            "failure_tags": tags,
            "confidence": 0.4,
        }

    if not packets:
        return {
            "response_type": "no_evidence",
            "message": "未找到足够依据支持该问题，请补充更多上下文或检查知识库覆盖。",
            "failure_tags": tags,
            "confidence": 0.0,
        }

    if mode == "refuse_if_insufficient" and (
        "no_recall" in tags or "gate_all_filtered" in tags or "admission_failure" in tags
    ):
        return {
            "response_type": "refuse",
            "message": "关键证据不足，暂不回答该问题。",
            "failure_tags": tags,
            "confidence": 0.0,
        }

    return {
        "response_type": "partial_evidence",
        "message": "仅找到部分相关证据，以下回答可能不完整。",
        "evidence_count": len(packets),
        "failure_tags": tags,
        "confidence": 0.55,
    }


def apply_insufficient_to_guard(
    grounding_result: dict[str, Any],
    insufficient: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(grounding_result)
    merged["insufficient_evidence"] = insufficient
    if insufficient.get("response_type") in ("no_evidence", "refuse", "conflicting_evidence"):
        merged["grounded"] = False
        merged["faithful"] = False
    return merged
