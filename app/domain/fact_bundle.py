"""FactBundle — capability-aware fact package for OMAW workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_fact_bundle_id(*, task_id: str, chapter_index: int | None, capability: str) -> str:
    ch = chapter_index if chapter_index is not None else 0
    suffix = uuid.uuid4().hex[:8]
    return f"fb-{task_id[:8]}-{ch}-{capability[:12]}-{suffix}"


@dataclass
class FactSource:
    type: str
    ref: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactSource:
        return cls(type=str(data.get("type") or ""), ref=str(data.get("ref") or ""))


@dataclass
class FactBundle:
    fact_bundle_id: str
    task_id: str
    chapter_index: Optional[int]
    capability: str
    agent: str = ""
    sources: list[FactSource] = field(default_factory=list)
    evidence_text: str = ""
    built_at: str = ""
    snapshot_version: str = ""
    rag_domains: list[str] = field(default_factory=list)
    rag_hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_bundle_id": self.fact_bundle_id,
            "task_id": self.task_id,
            "chapter_index": self.chapter_index,
            "capability": self.capability,
            "agent": self.agent,
            "sources": [s.to_dict() for s in self.sources],
            "evidence_text": self.evidence_text,
            "built_at": self.built_at or _now_iso(),
            "snapshot_version": self.snapshot_version,
            "rag_domains": list(self.rag_domains),
            "rag_hit_count": self.rag_hit_count,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional[FactBundle]:
        if not isinstance(data, dict):
            return None
        sources = [
            FactSource.from_dict(s)
            for s in (data.get("sources") or [])
            if isinstance(s, dict)
        ]
        return cls(
            fact_bundle_id=str(data.get("fact_bundle_id") or ""),
            task_id=str(data.get("task_id") or ""),
            chapter_index=data.get("chapter_index"),
            capability=str(data.get("capability") or ""),
            agent=str(data.get("agent") or ""),
            sources=sources,
            evidence_text=str(data.get("evidence_text") or ""),
            built_at=str(data.get("built_at") or ""),
            snapshot_version=str(data.get("snapshot_version") or ""),
            rag_domains=[str(d) for d in (data.get("rag_domains") or [])],
            rag_hit_count=int(data.get("rag_hit_count") or 0),
        )
