"""Unified command model for writing revision actions (edit_plot, etc.)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

TargetKind = Literal["outline", "body"]
ConfirmationStatus = Literal["pending", "approved", "rejected"]
RetryPolicy = Literal["no_auto_retry", "allow_retry"]


@dataclass
class WritingCommand:
    """Single source of truth for a writing revision action."""

    command_id: str
    action: str
    target_kind: TargetKind
    target_filename: str
    edit_spec: dict[str, Any] = field(default_factory=dict)
    write_spec: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = True
    confirmation_status: ConfirmationStatus = "pending"
    origin_turn: str = ""
    retry_policy: RetryPolicy = "no_auto_retry"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "action": self.action,
            "target_kind": self.target_kind,
            "target_filename": self.target_filename,
            "edit_spec": dict(self.edit_spec),
            "write_spec": dict(self.write_spec),
            "requires_confirmation": self.requires_confirmation,
            "confirmation_status": self.confirmation_status,
            "origin_turn": self.origin_turn,
            "retry_policy": self.retry_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WritingCommand:
        return cls(
            command_id=str(data.get("command_id") or uuid.uuid4().hex[:12]),
            action=str(data.get("action") or ""),
            target_kind=str(data.get("target_kind") or "outline"),  # type: ignore[arg-type]
            target_filename=str(data.get("target_filename") or ""),
            edit_spec=dict(data.get("edit_spec") or {}),
            write_spec=dict(data.get("write_spec") or {}),
            requires_confirmation=bool(data.get("requires_confirmation", True)),
            confirmation_status=str(data.get("confirmation_status") or "pending"),  # type: ignore[arg-type]
            origin_turn=str(data.get("origin_turn") or ""),
            retry_policy=str(data.get("retry_policy") or "no_auto_retry"),  # type: ignore[arg-type]
        )

    def signature(self) -> str:
        """Stable identity for deduping retries."""
        return f"{self.action}:{self.target_filename}:{self.edit_spec.get('old_text', '')}"
