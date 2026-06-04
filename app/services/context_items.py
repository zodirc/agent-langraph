"""Context governance primitives: ContextItem, ContextBucket, ContextEnvelope."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

ContextKind = Literal[
    "system",
    "user_turn",
    "recent_history",
    "semantic_summary",
    "working_memory",
    "episodic_memory",
    "knowledge",
    "tool_output",
    "file_slice",
    "symbol_slice",
    "git_diff",
    "diagnostic",
    "test_failure",
    "terminal_output",
    "workspace_summary",
]

ContextSource = Literal[
    "session",
    "memory",
    "retrieval",
    "tool",
    "workspace",
    "policy",
    "state",
]

ContextPriority = Literal["critical", "high", "medium", "low"]

ContextBucketName = Literal[
    "system_policy",
    "current_turn",
    "recent_transcript",
    "semantic_summary",
    "working_memory",
    "retrieved_memory",
    "retrieved_knowledge",
    "tool_observations",
    "file_context",
    "diagnostics",
]

ContextPurpose = Literal[
    "planning",
    "reasoning",
    "writing",
    "reviewing",
    "reflection",
    "summarization",
    "routing",
    "code_agent",
]

KIND_TO_BUCKET: dict[str, ContextBucketName] = {
    "system": "system_policy",
    "user_turn": "current_turn",
    "recent_history": "recent_transcript",
    "semantic_summary": "semantic_summary",
    "working_memory": "working_memory",
    "episodic_memory": "retrieved_memory",
    "knowledge": "retrieved_knowledge",
    "tool_output": "tool_observations",
    "file_slice": "file_context",
    "symbol_slice": "file_context",
    "git_diff": "file_context",
    "workspace_summary": "file_context",
    "diagnostic": "diagnostics",
    "test_failure": "diagnostics",
    "terminal_output": "diagnostics",
}


def new_context_id(prefix: str = "ctx") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ContextItem:
    """Minimal budgetable context unit (ADR §5.1)."""

    id: str
    kind: ContextKind
    source: ContextSource
    content: str
    role: str = "user"
    priority: ContextPriority = "medium"
    freshness: float = 1.0
    estimated_tokens: int = 0
    compressible: bool = True
    droppable: bool = True
    bucket: ContextBucketName | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def resolve_bucket(self) -> ContextBucketName:
        if self.bucket:
            return self.bucket
        return KIND_TO_BUCKET.get(self.kind, "recent_transcript")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "role": self.role,
            "content": self.content,
            "priority": self.priority,
            "freshness": self.freshness,
            "estimated_tokens": self.estimated_tokens,
            "compressible": self.compressible,
            "droppable": self.droppable,
            "bucket": self.resolve_bucket(),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_message(
        cls,
        msg: dict[str, Any],
        *,
        kind: ContextKind = "recent_history",
        source: ContextSource = "session",
        priority: ContextPriority = "medium",
        compressible: bool = True,
        droppable: bool = True,
    ) -> ContextItem:
        return cls(
            id=new_context_id(),
            kind=kind,
            source=source,
            role=str(msg.get("role") or "user"),
            content=str(msg.get("content") or ""),
            priority=priority,
            meta={"at": msg.get("at"), **(msg.get("meta") or {})},
            compressible=compressible,
            droppable=droppable,
        )


@dataclass
class ContextBucketAllocation:
    bucket: ContextBucketName
    budget_tokens: int
    initial_tokens: int = 0
    final_tokens: int = 0


@dataclass
class ContextEnvelope:
    """Single LLM-call context package (ADR §5.3)."""

    purpose: ContextPurpose
    model_name: str = ""
    token_budget_total: int = 0
    bucket_allocations: list[ContextBucketAllocation] = field(default_factory=list)
    items_kept: list[ContextItem] = field(default_factory=list)
    items_compressed: list[ContextItem] = field(default_factory=list)
    items_dropped: list[ContextItem] = field(default_factory=list)
    rendered_messages: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def conversation_history_for_payload(self) -> list[dict[str, Any]]:
        """Transcript-shaped slice for JSON user blobs (planning compat)."""
        out: list[dict[str, Any]] = []
        for item in self.items_kept:
            if item.kind in ("recent_history", "semantic_summary", "user_turn"):
                msg: dict[str, Any] = {
                    "role": item.role,
                    "content": item.content,
                }
                if item.meta.get("at"):
                    msg["at"] = item.meta["at"]
                if item.meta:
                    msg["meta"] = {k: v for k, v in item.meta.items() if k != "at"}
                out.append(msg)
        return out

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "model_name": self.model_name,
            "token_budget_total": self.token_budget_total,
            "bucket_allocations": [
                {
                    "bucket": a.bucket,
                    "budget_tokens": a.budget_tokens,
                    "initial_tokens": a.initial_tokens,
                    "final_tokens": a.final_tokens,
                }
                for a in self.bucket_allocations
            ],
            "kept_count": len(self.items_kept),
            "compressed_count": len(self.items_compressed),
            "dropped_count": len(self.items_dropped),
            "rendered_messages_count": len(self.rendered_messages),
            "trace": self.trace,
            "items_kept": [i.to_dict() for i in self.items_kept[:40]],
            "items_dropped": [i.to_dict() for i in self.items_dropped[:20]],
            "items_compressed": [i.to_dict() for i in self.items_compressed[:20]],
        }
