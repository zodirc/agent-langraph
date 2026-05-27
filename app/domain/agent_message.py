"""
A2A-style inter-agent messaging (Ch15) — protocol-agnostic message envelope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentCard:
    """Published agent identity and capabilities (A2A Agent Card sketch)."""

    agent_id: str
    name: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    risk_level: str = "LOW"
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "domains": list(self.domains),
            "risk_level": self.risk_level,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCard:
        return cls(
            agent_id=str(data.get("agent_id", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            capabilities=list(data.get("capabilities") or []),
            domains=list(data.get("domains") or []),
            risk_level=str(data.get("risk_level", "LOW")),
            version=str(data.get("version", "1")),
        )


@dataclass
class AgentMessage:
    """Cross-agent task message with capability-based routing."""

    message_id: str
    from_agent: str
    to_agent: str
    capability: str
    payload: dict[str, Any]
    correlation_id: str = ""
    message_type: str = "task"  # task | result | error
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "capability": self.capability,
            "payload": dict(self.payload),
            "correlation_id": self.correlation_id,
            "message_type": self.message_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMessage:
        return cls(
            message_id=str(data.get("message_id", uuid.uuid4())),
            from_agent=str(data.get("from_agent", "supervisor")),
            to_agent=str(data.get("to_agent", "")),
            capability=str(data.get("capability", "")),
            payload=dict(data.get("payload") or {}),
            correlation_id=str(data.get("correlation_id", "")),
            message_type=str(data.get("message_type", "task")),
            created_at=str(data.get("created_at", _now_iso())),
        )

    @classmethod
    def task(
        cls,
        *,
        from_agent: str,
        to_agent: str,
        capability: str,
        payload: dict[str, Any],
        correlation_id: str = "",
    ) -> AgentMessage:
        return cls(
            message_id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            capability=capability,
            payload=payload,
            correlation_id=correlation_id or str(uuid.uuid4()),
            message_type="task",
        )
