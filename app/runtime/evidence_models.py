"""Typed evidence-flow models for retrieval governance (target-state architecture)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RetrievalPurpose(str, Enum):
    FACT_QA = "fact_qa"
    COMPARATIVE_SUMMARY = "comparative_summary"
    PROCEDURAL_HOWTO = "procedural_howto"
    CODE_FIX = "code_fix"
    PLANNING_BACKGROUND = "planning_background"
    GENERAL_GROUNDED = "general_grounded"


class AnswerMode(str, Enum):
    STRICT_GROUNDED = "strict_grounded"
    BEST_EFFORT_GROUNDED = "best_effort_grounded"
    REFUSE_IF_INSUFFICIENT = "refuse_if_insufficient"


class TimeScope(str, Enum):
    LATEST = "latest"
    HISTORICAL = "historical"
    UNSPECIFIED = "unspecified"


class SupportType(str, Enum):
    DIRECT = "direct"
    CONTEXTUAL = "contextual"
    COMPARATIVE = "comparative"


class FailureTag(str, Enum):
    NO_RECALL = "no_recall"
    BAD_QUERY = "bad_query"
    LOW_RANK_RELEVANT = "low_rank_relevant"
    WRONG_GRANULARITY = "wrong_granularity"
    CONTEXT_DUPLICATION = "context_duplication"
    STALE_EVIDENCE = "stale_evidence"
    EVIDENCE_CONFLICT = "evidence_conflict"
    UNSUPPORTED_GENERATION = "unsupported_generation"
    FAKE_CITATION = "fake_citation"
    PURPOSE_MISMATCH = "purpose_mismatch"
    ADMISSION_FAILURE = "admission_failure"
    GATE_ALL_FILTERED = "gate_all_filtered"


class RetrievalDecision(BaseModel):
    """Task intent layer: whether and how to retrieve."""

    model_config = ConfigDict(extra="allow")

    need_retrieval: bool = True
    need_tools: bool = False
    purpose: str = RetrievalPurpose.GENERAL_GROUNDED.value
    freshness_required: bool = False
    authority_required: bool = False
    answer_mode: str = AnswerMode.BEST_EFFORT_GROUNDED.value
    skip_reason: str = ""
    debug_info: dict[str, Any] = Field(default_factory=dict)


class QueryObject(BaseModel):
    """Structured retrieval query compiled from user input and session constraints."""

    model_config = ConfigDict(extra="allow")

    original_query: str = ""
    standalone_query: str = ""
    must_have_terms: list[str] = Field(default_factory=list)
    soft_terms: list[str] = Field(default_factory=list)
    task_constraints: list[str] = Field(default_factory=list)
    time_scope: str = TimeScope.UNSPECIFIED.value
    source_scope: list[str] = Field(default_factory=list)
    debug_info: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="allow")

    dense: float = 0.0
    lexical: float = 0.0
    rrf: float = 0.0
    rerank: float = 0.0
    authority: float = 0.0
    freshness: float = 0.0
    task_match: float = 0.0
    total: float = 0.0


class CandidateEvidence(BaseModel):
    """Recall candidate with per-channel score breakdown."""

    model_config = ConfigDict(extra="allow")

    candidate_id: str = ""
    doc_id: str = ""
    chunk_id: str = ""
    source_id: str = ""
    title: str = ""
    content: str = ""
    source_type: str = "knowledge"
    authority_level: float = 0.5
    timestamp: Optional[str] = None
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    relevance_passed: bool = False
    filter_reason: str = ""
    retrieval_stage: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    debug_info: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_hit(cls, hit: dict[str, Any]) -> CandidateEvidence:
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        doc_id = str(hit.get("doc_id") or hit.get("id") or "")
        parent = str(meta.get("parent_doc_id") or doc_id)
        dense = float(hit.get("score") or 0.0) if hit.get("source") in ("chroma", "qdrant", "vector") else 0.0
        lexical = float(hit.get("score") or 0.0) if hit.get("source") == "keyword" else 0.0
        rrf = float(hit.get("rrf_score") or 0.0)
        rerank = float(hit.get("rerank_score") or hit.get("relevance_score") or 0.0)
        total = rerank or rrf or max(dense, lexical) or float(hit.get("score") or 0.0)
        return cls(
            candidate_id=doc_id,
            doc_id=doc_id,
            chunk_id=doc_id,
            source_id=parent,
            title=str(hit.get("title") or ""),
            content=str(hit.get("content") or hit.get("text") or ""),
            source_type=str(hit.get("source") or "knowledge"),
            score_breakdown=ScoreBreakdown(
                dense=dense,
                lexical=lexical,
                rrf=rrf,
                rerank=rerank,
                total=total,
            ),
            relevance_passed=bool(hit.get("relevance_passed", True)),
            filter_reason=str(hit.get("relevance_reason") or ""),
            retrieval_stage=str(hit.get("retrieval_stage") or ""),
            metadata=meta,
        )

    def to_hit(self) -> dict[str, Any]:
        """Convert back to legacy dict for downstream compatibility."""
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "content": self.content,
            "score": self.score_breakdown.dense or self.score_breakdown.total,
            "rerank_score": self.score_breakdown.rerank,
            "rrf_score": self.score_breakdown.rrf,
            "relevance_score": self.score_breakdown.total,
            "relevance_passed": self.relevance_passed,
            "relevance_reason": self.filter_reason,
            "retrieval_stage": self.retrieval_stage,
            "source": self.source_type,
            "metadata": self.metadata,
            "authority_level": self.authority_level,
            "score_breakdown": self.score_breakdown.model_dump(),
        }


class EvidencePacket(BaseModel):
    """Snippet-first unit injected into the generator."""

    model_config = ConfigDict(extra="allow")

    packet_id: str = ""
    snippet_text: str = ""
    source_id: str = ""
    chunk_id: str = ""
    char_range: Optional[tuple[int, int]] = None
    line_range: Optional[tuple[int, int]] = None
    support_type: str = SupportType.DIRECT.value
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    source_title: str = ""
    source_type: str = "knowledge"
    authority_level: float = 0.5
    timestamp: Optional[str] = None
    has_conflict: bool = False
    debug_info: dict[str, Any] = Field(default_factory=dict)


class EvidenceConflict(BaseModel):
    """Conflict between two evidence sources."""

    model_config = ConfigDict(extra="allow")

    field_key: str = ""
    value_a: str = ""
    value_b: str = ""
    source_a_id: str = ""
    source_b_id: str = ""
    source_a_type: str = ""
    source_b_type: str = ""
    priority_winner: str = ""
    arbitration_reason: str = ""
    unresolved: bool = False


class ClaimBinding(BaseModel):
    model_config = ConfigDict(extra="allow")

    claim_id: str = ""
    claim_text: str = ""
    citation_id: str = ""
    support_strength: str = "none"
    supported: bool = False


class GroundingCheckResult(BaseModel):
    """Post-generation claim-to-evidence validation."""

    model_config = ConfigDict(extra="allow")

    grounded: bool = True
    score: float = 1.0
    answer_mode: str = AnswerMode.BEST_EFFORT_GROUNDED.value
    bindings: list[ClaimBinding] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    fake_citations: list[str] = Field(default_factory=list)
    failure_tags: list[str] = Field(default_factory=list)
    debug_info: dict[str, Any] = Field(default_factory=dict)


class RetrievalTrace(BaseModel):
    """Per-turn observability bundle for the evidence pipeline."""

    model_config = ConfigDict(extra="allow")

    original_query: str = ""
    standalone_query: str = ""
    purpose: str = ""
    answer_mode: str = ""
    candidate_count: int = 0
    admitted_count: int = 0
    injected_count: int = 0
    filtered_reasons: dict[str, int] = Field(default_factory=dict)
    failure_tags: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    score_breakdown_sample: list[dict[str, Any]] = Field(default_factory=list)
    debug_info: dict[str, Any] = Field(default_factory=dict)
