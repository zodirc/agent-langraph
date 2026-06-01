"""
Turn event log — structured execution/decision/quality facts for one turn.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState


@dataclass
class TurnEvent:
    event_type: str
    subject: str
    timestamp: str
    node: str
    detail: dict[str, Any] = field(default_factory=dict)
    caused_by: Optional[str] = None


class TurnEventLog:
    """Collects turn-scoped events; serializable via to_dict()."""

    def __init__(self, events: Optional[list[TurnEvent]] = None) -> None:
        self._events: list[TurnEvent] = list(events or [])

    @classmethod
    def from_dict(cls, data: dict[str, Any] | list[Any] | None) -> TurnEventLog:
        if isinstance(data, TurnEventLog):
            return data
        if isinstance(data, list):
            return cls.from_events(data)
        if isinstance(data, dict):
            return cls.from_events(data.get("events") or [])
        return cls()

    @classmethod
    def from_events(cls, raw_events: list[Any]) -> TurnEventLog:
        events: list[TurnEvent] = []
        for item in raw_events or []:
            if isinstance(item, TurnEvent):
                events.append(item)
            elif isinstance(item, dict):
                events.append(
                    TurnEvent(
                        event_type=str(item.get("event_type") or ""),
                        subject=str(item.get("subject") or ""),
                        timestamp=str(item.get("timestamp") or ""),
                        node=str(item.get("node") or ""),
                        detail=dict(item.get("detail") or {}),
                        caused_by=item.get("caused_by"),
                    )
                )
        return cls(events)

    def record(
        self,
        event_type: str,
        subject: str,
        node: str,
        detail: Optional[dict[str, Any]] = None,
        *,
        caused_by: Optional[str] = None,
    ) -> TurnEvent:
        event = TurnEvent(
            event_type=event_type,
            subject=subject,
            timestamp=datetime.now(timezone.utc).isoformat(),
            node=node,
            detail=dict(detail or {}),
            caused_by=caused_by,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> list[TurnEvent]:
        return list(self._events)

    def events_as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._events]

    def execution_facts(self) -> list[dict[str, Any]]:
        return [
            asdict(e)
            for e in self._events
            if e.event_type.startswith("tool_") or e.event_type.startswith("artifact_")
        ]

    def decision_facts(self) -> list[dict[str, Any]]:
        return [
            asdict(e)
            for e in self._events
            if e.event_type.startswith("plan_") or e.event_type.startswith("route_")
        ]

    def quality_facts(self) -> list[dict[str, Any]]:
        return [
            asdict(e)
            for e in self._events
            if e.event_type.startswith("verify_")
            or e.event_type.startswith("repair_")
            or e.event_type.startswith("review_")
        ]

    def failure_events(self) -> list[TurnEvent]:
        markers = ("error", "failed", "blocked", "rejected")
        return [
            e
            for e in self._events
            if any(m in e.event_type.lower() for m in markers)
        ]

    def to_summary(self, state: Optional[AgentState] = None) -> dict[str, Any]:
        """Build summary fields compatible with legacy build_turn_facts()."""
        from app.services.fact_layer import build_turn_facts

        base = build_turn_facts(state) if state else {}
        return {
            **base,
            "event_count": len(self._events),
            "has_failures": bool(self.failure_events()) or bool(base.get("has_failures")),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"events": self.events_as_dicts()}


def get_turn_event_log(state: AgentState) -> TurnEventLog:
    return TurnEventLog.from_dict(state.get("turn_event_log"))


def record_turn_event(
    state: AgentState,
    event_type: str,
    subject: str,
    node: str,
    detail: Optional[dict[str, Any]] = None,
    *,
    caused_by: Optional[str] = None,
) -> AgentState:
    """Append an event and persist log on state."""
    from app.runtime.state import merge_state

    log = get_turn_event_log(state)
    log.record(event_type, subject, node, detail, caused_by=caused_by)
    return merge_state(state, turn_event_log=log.to_dict())
