"""Evidence assembly: admission, snippet-first packets, dedup and coverage."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import (
    CandidateEvidence,
    EvidencePacket,
    FailureTag,
    QueryObject,
    RetrievalDecision,
    ScoreBreakdown,
    SupportType,
)
from app.services.evidence_arbitration import annotate_authority
from app.services.retrieval_search_policy import recency_score
from app.services.snippet_extractor import extract_snippet
from app.services.relevance_gate import relevance_score

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


def purpose_admission_threshold(purpose: str) -> float:
    mapping = getattr(settings, "RETRIEVAL_PURPOSE_THRESHOLDS", None) or {}
    if isinstance(mapping, dict) and purpose in mapping:
        return float(mapping[purpose])
    defaults = {
        "fact_qa": 0.45,
        "comparative_summary": 0.25,
        "procedural_howto": 0.35,
        "code_fix": 0.40,
        "planning_background": 0.30,
        "general_grounded": 0.30,
    }
    base = float(getattr(settings, "RAG_RERANK_MIN_SCORE", 0.25))
    purpose_floor = defaults.get(purpose, base)
    return max(base, purpose_floor)


def purpose_top_k(purpose: str) -> int:
    mapping = getattr(settings, "RETRIEVAL_PURPOSE_TOP_K", None) or {}
    if isinstance(mapping, dict) and purpose in mapping:
        return int(mapping[purpose])
    defaults = {
        "fact_qa": 5,
        "comparative_summary": 10,
        "procedural_howto": 8,
        "code_fix": 6,
        "planning_background": 6,
        "general_grounded": 8,
    }
    return defaults.get(purpose, int(getattr(settings, "RETRIEVAL_TOP_K", 8)))


def hits_to_candidates(hits: list[dict[str, Any]]) -> list[CandidateEvidence]:
    return [CandidateEvidence.from_hit(h) for h in hits if isinstance(h, dict)]


def _must_have_satisfied(cand: CandidateEvidence, must_have: list[str]) -> bool:
    if not must_have:
        return True
    corpus = f"{cand.title} {cand.content}".lower()
    hits = sum(1 for t in must_have if t.lower() in corpus)
    return hits >= max(1, len(must_have) // 2)


def _task_match_score(cand: CandidateEvidence, constraints: list[str]) -> float:
    text = f"{cand.title} {cand.content}".lower()
    score = 0.0
    if "needs_steps" in constraints:
        if re.search(r"(?i)\b(step\s*\d|^\d+\.|首先|然后|最后)", text, re.M):
            score += 0.3
    if "needs_code_fix" in constraints:
        if re.search(r"(?i)\b(traceback|error|exception|```)", text):
            score += 0.35
    if "needs_single_evidence" in constraints:
        score += 0.1
    return min(score, 1.0)


def apply_admission_gate(
    candidates: list[CandidateEvidence],
    query: QueryObject,
    decision: RetrievalDecision,
) -> tuple[list[CandidateEvidence], list[CandidateEvidence], dict[str, int]]:
    """Filter candidates; return (admitted, rejected, filter_reason_counts)."""
    if not getattr(settings, "RETRIEVAL_ENABLE_ADMISSION_GATE", True):
        return candidates, [], {}

    threshold = purpose_admission_threshold(decision.purpose)
    mode = str(getattr(settings, "RETRIEVAL_ADMISSION_GATE_MODE", "soft")).lower()
    candidates = annotate_freshness(candidates, query)
    admitted, rejected, reason_counts = _admission_pass(
        candidates, query, decision, threshold, mode=mode
    )
    if getattr(settings, "RETRIEVAL_SHADOW_ADMISSION_GATE", True) and mode == "soft":
        shadow_stats = shadow_admission_eval(candidates, query, decision)
        reason_counts.update(shadow_stats)
    return admitted, rejected, reason_counts


def _admission_pass(
    candidates: list[CandidateEvidence],
    query: QueryObject,
    decision: RetrievalDecision,
    threshold: float,
    *,
    mode: str,
) -> tuple[list[CandidateEvidence], list[CandidateEvidence], dict[str, int]]:
    """Core admission logic; extracted for shadow evaluation."""
    reason_counts: dict[str, int] = {}
    admitted: list[CandidateEvidence] = []
    rejected: list[CandidateEvidence] = []
    annotated = annotate_authority(list(candidates))
    seen_sources: set[str] = set()
    for cand in annotated:
        score = relevance_score(cand.to_hit())
        cand.score_breakdown.rerank = score
        cand.score_breakdown.task_match = _task_match_score(cand, query.task_constraints)
        cand.score_breakdown.authority = cand.authority_level
        cand.score_breakdown.freshness = recency_score(cand.metadata or {})
        novelty = 1.0 if cand.source_id not in seen_sources else 0.3
        seen_sources.add(cand.source_id)
        cand.score_breakdown.total = (
            0.4 * score
            + 0.2 * cand.authority_level
            + 0.15 * cand.score_breakdown.task_match
            + 0.15 * cand.score_breakdown.freshness
            + 0.1 * novelty
        )
        reason = ""
        if score < threshold:
            reason = "below_threshold"
        elif query.must_have_terms and not _must_have_satisfied(cand, query.must_have_terms):
            reason = "must_have_miss"
        elif decision.freshness_required and cand.metadata.get("stale"):
            reason = "stale_evidence"
        if reason:
            cand.relevance_passed = False
            cand.filter_reason = reason
            rejected.append(cand)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if mode == "hard":
                continue
        cand.relevance_passed = True
        cand.filter_reason = cand.filter_reason or "admitted"
        admitted.append(cand)
    admitted.sort(key=lambda c: c.score_breakdown.total, reverse=True)
    top_k = purpose_top_k(decision.purpose)
    if mode == "hard":
        admitted = admitted[:top_k]
    elif not admitted and rejected:
        admitted = sorted(rejected, key=lambda c: c.score_breakdown.total, reverse=True)[:1]
        for c in admitted:
            c.relevance_passed = True
            c.filter_reason = "soft_fallback"
        reason_counts["soft_fallback"] = len(admitted)
    return admitted, rejected, reason_counts


def shadow_admission_eval(
    candidates: list[CandidateEvidence],
    query: QueryObject,
    decision: RetrievalDecision,
) -> dict[str, int]:
    """Shadow eval: what hard gate would admit/reject without changing behavior."""
    threshold = purpose_admission_threshold(decision.purpose)
    admitted, rejected, reasons = _admission_pass(
        candidates, query, decision, threshold, mode="hard"
    )
    return {
        "shadow_would_admit": len(admitted),
        "shadow_would_reject": len(rejected),
        **{f"shadow_{k}": v for k, v in reasons.items()},
    }


def annotate_freshness(candidates: list[CandidateEvidence], query: QueryObject) -> list[CandidateEvidence]:
    """Mark stale candidates when latest scope is required."""
    if query.time_scope != "latest":
        return candidates
    for cand in candidates:
        meta = cand.metadata or {}
        if meta.get("deprecated") or meta.get("superseded"):
            meta = dict(meta)
            meta["stale"] = True
            cand.metadata = meta
    return candidates


def _expand_snippet_neighborhood(content: str, snippet: str, *, radius: int = 1) -> tuple[str, tuple[int, int] | None]:
    """Expand snippet with adjacent sentences when too short."""
    min_chars = 80
    if len(snippet) >= min_chars or radius <= 0:
        return snippet, (0, len(snippet)) if snippet else None
    sentences = [s.strip() for s in _SENTENCE_RE.findall(content) if len(s.strip()) > 8]
    if len(sentences) <= 1:
        return snippet, (0, len(snippet)) if snippet else None
    best_idx = 0
    best_overlap = 0.0
    snippet_tokens = _TOKEN_RE.findall(snippet.lower())
    for i, sent in enumerate(sentences):
        sent_tokens = _TOKEN_RE.findall(sent.lower())
        if not sent_tokens:
            continue
        overlap = len(set(snippet_tokens) & set(sent_tokens)) / max(len(sent_tokens), 1)
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = i
    start = max(0, best_idx - radius)
    end = min(len(sentences), best_idx + radius + 1)
    expanded = " ".join(sentences[start:end])
    if len(expanded) > len(content) * 0.6:
        return snippet, (0, len(snippet)) if snippet else None
    return expanded, (start, end)


def detect_duplication_failures(
    candidates: list[CandidateEvidence],
    admitted: list[CandidateEvidence],
) -> list[str]:
    """Tag context duplication when dedup removed significant fraction."""
    if len(candidates) < 2:
        return []
    dropped = len(candidates) - len(admitted)
    if dropped >= 2 and dropped / len(candidates) >= 0.4:
        return [FailureTag.CONTEXT_DUPLICATION.value]
    return []


def detect_purpose_mismatch(
    decision: RetrievalDecision,
    admitted: list[CandidateEvidence],
    query: QueryObject,
) -> list[str]:
    """Tag when admitted evidence lacks task-structure match for purpose."""
    if not admitted:
        return []
    if decision.purpose == "procedural_howto":
        has_steps = any(
            c.score_breakdown.task_match >= 0.25 for c in admitted
        )
        if not has_steps and "needs_steps" in query.task_constraints:
            return [FailureTag.PURPOSE_MISMATCH.value]
    if decision.purpose == "code_fix":
        has_code = any(c.score_breakdown.task_match >= 0.3 for c in admitted)
        if not has_code:
            return [FailureTag.PURPOSE_MISMATCH.value]
    return []


def _embedding_too_similar(text: str, others: list[str], *, threshold: float = 0.92) -> bool:
    try:
        from app.services.embedding_service import embed_text
        import math

        vec = embed_text(text[:800])
        for other in others:
            ovec = embed_text(other[:800])
            if len(vec) != len(ovec):
                continue
            dot = sum(a * b for a, b in zip(vec, ovec))
            na = math.sqrt(sum(a * a for a in vec))
            nb = math.sqrt(sum(b * b for b in ovec))
            if na > 0 and nb > 0 and dot / (na * nb) >= threshold:
                return True
    except Exception:
        return False
    return False


def apply_token_budget(packets: list[EvidencePacket], *, max_tokens: int | None = None) -> list[EvidencePacket]:
    """Cap injected evidence by approximate token budget (§6.1 resource_budget linkage)."""
    if not max_tokens or max_tokens <= 0:
        return packets
    kept: list[EvidencePacket] = []
    used = 0
    for pkt in packets:
        est = max(1, len(pkt.snippet_text) // 4)
        if used + est > max_tokens:
            break
        kept.append(pkt)
        used += est
    return kept


def _text_overlap(a: str, b: str) -> float:
    ta = {t.lower() for t in _TOKEN_RE.findall(a) if len(t) > 2}
    tb = {t.lower() for t in _TOKEN_RE.findall(b) if len(t) > 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def deduplicate_candidates(
    candidates: list[CandidateEvidence],
    *,
    max_per_source: int | None = None,
) -> list[CandidateEvidence]:
    cap = max_per_source or int(getattr(settings, "RETRIEVAL_MAX_CHUNKS_PER_SOURCE", 2))
    by_source: dict[str, list[CandidateEvidence]] = {}
    for cand in candidates:
        by_source.setdefault(cand.source_id, []).append(cand)

    result: list[CandidateEvidence] = []
    for _source, bucket in by_source.items():
        kept: list[CandidateEvidence] = []
        for cand in sorted(bucket, key=lambda c: c.score_breakdown.total, reverse=True):
            if len(kept) >= cap:
                break
            if any(_text_overlap(cand.content, k.content) > 0.85 for k in kept):
                continue
            if getattr(settings, "RETRIEVAL_EMBEDDING_DEDUP_ENABLED", False):
                if _embedding_too_similar(cand.content, [k.content for k in kept]):
                    continue
            kept.append(cand)
        result.extend(kept)
    result.sort(key=lambda c: c.score_breakdown.total, reverse=True)
    return result


def enforce_source_diversity(
    candidates: list[CandidateEvidence],
    decision: RetrievalDecision,
) -> list[CandidateEvidence]:
    if decision.purpose != "comparative_summary":
        return candidates
    min_sources = int(getattr(settings, "RETRIEVAL_MIN_SOURCE_DIVERSITY", 2))
    seen: set[str] = set()
    diverse: list[CandidateEvidence] = []
    rest: list[CandidateEvidence] = []
    for cand in candidates:
        if cand.source_id not in seen:
            diverse.append(cand)
            seen.add(cand.source_id)
        else:
            rest.append(cand)
    while len(seen) < min_sources and rest:
        cand = rest.pop(0)
        if cand.source_id not in seen:
            diverse.append(cand)
            seen.add(cand.source_id)
    diverse.extend(rest)
    return diverse


def _support_type_for_purpose(purpose: str) -> str:
    if purpose == "comparative_summary":
        return SupportType.COMPARATIVE.value
    if purpose == "planning_background":
        return SupportType.CONTEXTUAL.value
    return SupportType.DIRECT.value


def assemble_evidence_packets(
    candidates: list[CandidateEvidence],
    query: QueryObject,
    decision: RetrievalDecision,
) -> list[EvidencePacket]:
    """Build snippet-first evidence packets from admitted candidates."""
    if not getattr(settings, "RETRIEVAL_ENABLE_SNIPPET_FIRST", True):
        return _packets_from_full_chunks(candidates, decision)

    max_chars = int(getattr(settings, "RETRIEVAL_SNIPPET_MAX_CHARS", 2000))
    packets: list[EvidencePacket] = []
    support = _support_type_for_purpose(decision.purpose)

    radius = int(getattr(settings, "RETRIEVAL_NEIGHBORHOOD_EXPAND_RADIUS", 1))
    dual_track = bool(getattr(settings, "RETRIEVAL_SNIPPET_DUAL_TRACK", False))
    max_sent = int(getattr(settings, "RAG_SNIPPET_MAX_SENTENCES", 5))
    for cand in candidates:
        snippet, char_range = extract_snippet(
            cand.content,
            query.standalone_query,
            title=cand.title,
            purpose=decision.purpose,
            max_sentences=max_sent,
            include_summary=dual_track,
        )
        if radius > 0 and len(snippet) < 80:
            snippet, char_range = _expand_snippet_neighborhood(
                cand.content, snippet, radius=radius
            )
        snippet = snippet[:max_chars]
        packets.append(
            EvidencePacket(
                packet_id=str(uuid.uuid4())[:12],
                snippet_text=snippet,
                source_id=cand.source_id,
                chunk_id=cand.chunk_id,
                char_range=char_range,
                support_type=support,
                score_breakdown=cand.score_breakdown,
                source_title=cand.title,
                source_type=cand.source_type,
                authority_level=cand.authority_level,
                timestamp=cand.timestamp,
            )
        )
    return packets


def _packets_from_full_chunks(
    candidates: list[CandidateEvidence],
    decision: RetrievalDecision,
) -> list[EvidencePacket]:
    support = _support_type_for_purpose(decision.purpose)
    max_chars = int(getattr(settings, "RETRIEVAL_SNIPPET_MAX_CHARS", 2000))
    return [
        EvidencePacket(
            packet_id=str(uuid.uuid4())[:12],
            snippet_text=cand.content[:max_chars],
            source_id=cand.source_id,
            chunk_id=cand.chunk_id,
            support_type=support,
            score_breakdown=cand.score_breakdown,
            source_title=cand.title,
            source_type=cand.source_type,
            authority_level=cand.authority_level,
        )
        for cand in candidates
    ]


def packets_to_hits(packets: list[EvidencePacket], candidates: list[CandidateEvidence]) -> list[dict[str, Any]]:
    """Convert packets back to legacy hit dicts for state compatibility."""
    by_chunk = {c.chunk_id: c for c in candidates}
    hits: list[dict[str, Any]] = []
    for pkt in packets:
        cand = by_chunk.get(pkt.chunk_id)
        hit = cand.to_hit() if cand else {}
        hit["content"] = pkt.snippet_text
        hit["evidence_packet_id"] = pkt.packet_id
        hit["support_type"] = pkt.support_type
        hit["authority_level"] = pkt.authority_level
        hit["has_conflict"] = pkt.has_conflict
        hits.append(hit)
    return hits


def classify_assembly_failures(
    *,
    raw_count: int,
    admitted_count: int,
    packet_count: int,
    filter_reasons: dict[str, int],
    decision: RetrievalDecision,
) -> list[str]:
    tags: list[str] = []
    if raw_count == 0 and decision.need_retrieval:
        tags.append(FailureTag.NO_RECALL.value)
    if filter_reasons.get("gate_all_filtered") or (raw_count > 0 and admitted_count == 0):
        tags.append(FailureTag.ADMISSION_FAILURE.value)
        tags.append(FailureTag.GATE_ALL_FILTERED.value)
    if filter_reasons.get("must_have_miss"):
        tags.append(FailureTag.BAD_QUERY.value)
    if filter_reasons.get("stale_evidence"):
        tags.append(FailureTag.STALE_EVIDENCE.value)
    if filter_reasons.get("below_threshold", 0) > admitted_count:
        tags.append(FailureTag.LOW_RANK_RELEVANT.value)
    if packet_count < admitted_count:
        tags.append(FailureTag.WRONG_GRANULARITY.value)
    return tags
