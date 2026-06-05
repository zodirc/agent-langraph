"""Failure attribution chain for observability dashboard (§4.6.3)."""

from __future__ import annotations

from typing import Any

ATTRIBUTION_STAGES = (
    "query_construction",
    "recall",
    "rerank",
    "admission",
    "context_assembly",
    "generation_grounding",
    "citation_validation",
)

_TAG_TO_STAGE: dict[str, str] = {
    "bad_query": "query_construction",
    "no_recall": "recall",
    "low_rank_relevant": "rerank",
    "admission_failure": "admission",
    "gate_all_filtered": "admission",
    "stale_evidence": "admission",
    "context_duplication": "context_assembly",
    "wrong_granularity": "context_assembly",
    "purpose_mismatch": "context_assembly",
    "evidence_conflict": "context_assembly",
    "unsupported_generation": "generation_grounding",
    "fake_citation": "citation_validation",
}


def attribute_failures(
    *,
    retrieval_trace: dict[str, Any] | None = None,
    grounding_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map failure tags to pipeline stages for dashboard."""
    tags: list[str] = []
    if retrieval_trace:
        tags.extend(retrieval_trace.get("failure_tags") or [])
    if grounding_result:
        tags.extend(grounding_result.get("failure_tags") or [])

    tags = list(dict.fromkeys(tags))
    stages: dict[str, list[str]] = {s: [] for s in ATTRIBUTION_STAGES}
    for tag in tags:
        stage = _TAG_TO_STAGE.get(tag, "context_assembly")
        stages[stage].append(tag)

    primary = ""
    for stage in ATTRIBUTION_STAGES:
        if stages[stage]:
            primary = stage
            break

    return {
        "primary_stage": primary,
        "stages": {k: v for k, v in stages.items() if v},
        "failure_tags": tags,
        "candidate_count": (retrieval_trace or {}).get("candidate_count"),
        "admitted_count": (retrieval_trace or {}).get("admitted_count"),
        "grounded": (grounding_result or {}).get("grounded", (grounding_result or {}).get("faithful")),
    }


def dashboard_rows(attribution: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten for dashboard / log export."""
    rows: list[dict[str, str]] = []
    for stage, tags in (attribution.get("stages") or {}).items():
        for tag in tags:
            rows.append({"stage": stage, "tag": tag, "primary": str(stage == attribution.get("primary_stage"))})
    return rows
