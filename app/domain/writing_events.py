"""Writing command lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventKind = Literal[
    "command_built",
    "confirmation_pending",
    "confirmation_approved",
    "confirmation_rejected",
    "execution_started",
    "execution_succeeded",
    "execution_failed",
    "retry_blocked",
]


@dataclass
class WritingCommandEvent:
    kind: EventKind
    command_id: str
    action: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "command_id": self.command_id,
            "action": self.action,
            "detail": dict(self.detail),
        }
