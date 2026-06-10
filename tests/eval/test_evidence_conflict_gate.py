"""Conflict evidence arbitration gate (§4.2)."""

import json
from pathlib import Path

from app.services.evidence_arbitration import detect_conflicts
from app.services.evidence_assembly import hits_to_candidates

_CASES = Path(__file__).resolve().parent / "data" / "conflict_evidence_cases.json"


def _load_cases() -> list[dict]:
    return list(json.loads(_CASES.read_text(encoding="utf-8")).get("cases") or [])


def test_conflict_evidence_marked():
    case = _load_cases()[0]
    hits = [
        {
            "doc_id": f"{case['id']}_{i}",
            "content": src["content"],
            "title": src["source"],
            "score": src["score"],
            "metadata": {"source": src["source"]},
        }
        for i, src in enumerate(case["sources"])
    ]
    candidates = hits_to_candidates(hits)
    conflicts = detect_conflicts(candidates)
    mark_rate = 1.0 if conflicts else 0.0
    assert mark_rate >= case["min_conflict_mark_rate"]
