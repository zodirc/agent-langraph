"""Revision intent — first-class structure aligned with edit_text_artifact parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RevisionScope = Literal["full", "chapter", "section", "paragraph", "sentence", "span"]
OperationType = Literal["polish", "expand", "compress", "rewrite", "fix", "retone", "restructure"]
OutputMode = Literal["diff", "fragment", "full"]
CompletionPolicy = Literal["stop_after_edit", "propose_next", "batch_until_done"]


@dataclass
class RevisionEdit:
    """Maps 1:1 to handle_edit_text_artifact parameters."""

    old_text: str = ""
    new_text: str = ""
    occurrence_index: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    replace_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.old_text:
            out["old_text"] = self.old_text
        if self.new_text:
            out["new_text"] = self.new_text
        if self.occurrence_index is not None:
            out["occurrence_index"] = self.occurrence_index
        if self.start_line is not None:
            out["start_line"] = self.start_line
        if self.end_line is not None:
            out["end_line"] = self.end_line
        if self.replace_all:
            out["replace_all"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RevisionEdit:
        raw = dict(data or {})
        occ = raw.get("occurrence_index")
        sl = raw.get("start_line")
        el = raw.get("end_line")
        return cls(
            old_text=str(raw.get("old_text") or ""),
            new_text=str(raw.get("new_text") or ""),
            occurrence_index=int(occ) if occ is not None else None,
            start_line=int(sl) if sl is not None else None,
            end_line=int(el) if el is not None else None,
            replace_all=bool(raw.get("replace_all", False)),
        )


@dataclass
class RevisionIntent:
    artifact_filename: str
    artifact_role: str = "body"
    revision_scope: RevisionScope = "span"
    target_sections: list[str] = field(default_factory=list)
    operation_type: OperationType = "polish"
    edits: list[RevisionEdit] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    preserve_requirements: list[str] = field(default_factory=list)
    output_mode: OutputMode = "diff"
    replaces_active_goal: bool = True
    completion_policy: CompletionPolicy = "stop_after_edit"
    source: str = "llm"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_filename": self.artifact_filename,
            "artifact_role": self.artifact_role,
            "revision_scope": self.revision_scope,
            "target_sections": list(self.target_sections),
            "operation_type": self.operation_type,
            "edits": [e.to_dict() for e in self.edits],
            "constraints": list(self.constraints),
            "preserve_requirements": list(self.preserve_requirements),
            "output_mode": self.output_mode,
            "replaces_active_goal": self.replaces_active_goal,
            "completion_policy": self.completion_policy,
            "source": self.source,
            "confidence": round(float(self.confidence), 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RevisionIntent | None:
        if not isinstance(data, dict) or not data:
            return None
        filename = str(data.get("artifact_filename") or "").strip()
        if not filename:
            return None
        edits_raw = data.get("edits") or []
        edits = [
            RevisionEdit.from_dict(e)
            for e in edits_raw
            if isinstance(e, dict)
        ]
        return cls(
            artifact_filename=filename,
            artifact_role=str(data.get("artifact_role") or "body"),
            revision_scope=str(data.get("revision_scope") or "span"),  # type: ignore[arg-type]
            target_sections=[str(s) for s in (data.get("target_sections") or [])],
            operation_type=str(data.get("operation_type") or "polish"),  # type: ignore[arg-type]
            edits=edits,
            constraints=[str(c) for c in (data.get("constraints") or [])],
            preserve_requirements=[str(p) for p in (data.get("preserve_requirements") or [])],
            output_mode=str(data.get("output_mode") or "diff"),  # type: ignore[arg-type]
            replaces_active_goal=bool(data.get("replaces_active_goal", True)),
            completion_policy=str(data.get("completion_policy") or "stop_after_edit"),  # type: ignore[arg-type]
            source=str(data.get("source") or "llm"),
            confidence=float(data.get("confidence") or 0.0),
        )

    @property
    def revision_summary(self) -> str:
        sections = ", ".join(self.target_sections[:3])
        return f"{self.operation_type}:{self.revision_scope}:{sections or self.artifact_filename}"
