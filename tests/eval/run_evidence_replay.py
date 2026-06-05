#!/usr/bin/env python3
"""Replay evidence pipeline against labeled query cases (§9.3 / §9.6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DATA = Path(__file__).parent / "data" / "evidence_replay_cases.json"


def _load_cases() -> list[dict]:
    return json.loads(_DATA.read_text(encoding="utf-8"))


def replay_case(case: dict) -> dict:
    from app.runtime.evidence_models import RetrievalDecision
    from app.services.evidence_pipeline import run_evidence_pipeline
    from app.services.retrieval_decision import build_retrieval_decision

    state = {
        "task_id": f"replay-{case['id']}",
        "session_id": "replay",
        "user_id": "eval",
        "task_type": "qa",
        "skip_retrieval": False,
        "selected_tools": ["replay"],
        "input_payload": {"goal": case["query"]},
        "conversation_history": case.get("history", []),
    }
    decision = build_retrieval_decision(state)
    overrides = {"need_retrieval": True, "skip_reason": ""}
    if case.get("purpose"):
        overrides["purpose"] = case["purpose"]
    if case.get("time_scope") == "latest":
        overrides["freshness_required"] = True
    decision = RetrievalDecision(**{**decision.model_dump(), **overrides})

    relevant_ids = case.get("relevant_ids") or []
    raw_hits = []
    for idx, doc in enumerate(case.get("corpus", [])):
        doc_id = relevant_ids[idx] if idx < len(relevant_ids) else f"doc_{idx}"
        raw_hits.append(
            {
                "doc_id": doc_id,
                "title": doc.get("title", ""),
                "content": doc.get("content", ""),
                "rerank_score": 0.8 - idx * 0.05,
                "relevance_passed": True,
                "metadata": doc.get("metadata", {}),
                "source": doc.get("source_type", "knowledge"),
            }
        )

    # Inject pre-built decision via state so pipeline respects replay purpose.
    state["retrieval_decision"] = decision.model_dump()
    patch = run_evidence_pipeline(state, raw_hits)
    trace = patch.get("retrieval_trace") or {}
    injected_ids = [h.get("doc_id") for h in patch.get("retrieved_knowledge") or []]
    relevant = set(case.get("relevant_ids") or [])
    recall = len(relevant & set(injected_ids)) / len(relevant) if relevant else 1.0

    return {
        "id": case["id"],
        "purpose": trace.get("purpose"),
        "failure_tags": trace.get("failure_tags"),
        "admitted": trace.get("admitted_count"),
        "injected": trace.get("injected_count"),
        "recall_proxy": round(recall, 3),
        "shadow_filter": (trace.get("filtered_reasons") or {}).get("shadow_would_reject"),
    }


def main() -> int:
    results = [replay_case(c) for c in _load_cases()]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    failures = [r for r in results if r.get("recall_proxy", 0) < 0.5]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
