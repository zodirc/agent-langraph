"""Standardized writing intent — Intent layer output (no tools, no filenames)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

IntentAction = Literal[
    "write_outline",
    "edit_plot",
    "review_outline",
    "reset_body",
    "write_body",
    "rewrite_outline",
    "run_tools",
    "pause",
    "continue",
    "batch_unit_quality",
]

TargetHint = Literal["outline", "body", ""]


@dataclass
class IntentAnchor:
    """Optional edit anchors — filename binding happens in Command Builder."""

    old_text: str = ""
    new_text: str = ""
    steer_correction: str = ""
    target_hint: TargetHint = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.old_text:
            out["old_text"] = self.old_text
        if self.new_text:
            out["new_text"] = self.new_text
        if self.steer_correction:
            out["steer_correction"] = self.steer_correction
        if self.target_hint:
            out["target_hint"] = self.target_hint
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IntentAnchor:
        raw = dict(data or {})
        hint = str(raw.get("target_hint") or "").strip().lower()
        if hint not in ("outline", "body"):
            hint = ""
        return cls(
            old_text=str(raw.get("old_text") or ""),
            new_text=str(raw.get("new_text") or ""),
            steer_correction=str(raw.get("steer_correction") or ""),
            target_hint=hint,  # type: ignore[arg-type]
        )


@dataclass
class WritingIntentRecord:
    """Single intent object consumed by Command Builder."""

    action: str
    force: bool = False
    reason: str = ""
    anchor: IntentAnchor = field(default_factory=IntentAnchor)
    source: str = "planning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "force": self.force,
            "reason": self.reason,
            "anchor": self.anchor.to_dict(),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WritingIntentRecord:
        return cls(
            action=str(data.get("action") or ""),
            force=bool(data.get("force", False)),
            reason=str(data.get("reason") or ""),
            anchor=IntentAnchor.from_dict(data.get("anchor") if isinstance(data.get("anchor"), dict) else data.get("intent_anchor")),
            source=str(data.get("source") or "planning"),
        )
