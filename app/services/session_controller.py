"""Unified session ingress — single message channel (optimization.md Phase A/D).

Users send one message; backend classifies and dispatches internally.
Client routing hints (preempt, replace_goal) are stripped — FSM is sole authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Optional

from app.runtime.state import AgentState, merge_state
from app.services.control_payload import merge_stripped_message_payload
from app.services.event_classification import EventClassification, resolve_inbound_event
from app.services.session_turn import build_inbound_merged_payload
from app.services.graph_runner import GraphRunner, get_graph_runner
from app.services.run_controller import RunController
from app.services.session_fsm import (
    FSM_REPLANNING,
    get_fsm_state,
    stamp_session_mode,
    sync_fsm_state,
)
from app.services.state_store import get_state_store


def _check_idempotent(state: AgentState, client_message_id: Optional[str]) -> bool:
    """True when this message was already applied."""
    if not client_message_id:
        return False
    payload = state.get("input_payload") or {}
    return str(payload.get("last_applied_message_id") or "") == str(client_message_id)


def _stamp_applied(state: AgentState, client_message_id: Optional[str]) -> AgentState:
    if not client_message_id:
        return state
    payload = dict(state.get("input_payload") or {})
    payload["last_applied_message_id"] = str(client_message_id)
    return merge_state(state, input_payload=payload)


def _apply_fsm_dispatch_override(
    event: EventClassification,
    state: AgentState,
) -> EventClassification:
    """Resume under REPLANNING auto-upgrades to redirect."""
    fsm = get_fsm_state(state)
    if event.event_type == "resume" and fsm == FSM_REPLANNING:
        return EventClassification(
            event_type="redirect",
            event_id=event.event_id,
            source="fsm_replanning_resume_override",
            reason="resume while REPLANNING → auto redirect (no awaits supersede deadlock)",
        )
    return event


def _resolve_inbound_dispatch(
    state: AgentState,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], EventClassification]:
    """Build inbound payload + L1 classification (same path as prepare_session_turn)."""
    inbound = build_inbound_merged_payload(state, payload)
    prospective_turn = int(state.get("session_turn") or 0) + 1
    classify_state = merge_state(
        state,
        conversation_history=inbound.get("conversation_history") or state.get("conversation_history"),
        session_turn=prospective_turn,
    )
    event = _apply_fsm_dispatch_override(
        resolve_inbound_event(classify_state, payload=inbound),
        state,
    )
    return inbound, event


class SessionController:
    """L0 ingress: handle_message is the only routing authority."""

    def __init__(self, runner: Optional[GraphRunner] = None) -> None:
        self._runner = runner or get_graph_runner()

    def handle_message(
        self,
        task_id: str,
        text: str,
        *,
        client_message_id: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
        confirm: bool = False,
        intervention: Optional[dict[str, Any]] = None,
        priority: int = 0,
        user_id: str = "anonymous",
        task_type: str = "qa",
        new_session: bool = False,
        # Deprecated — ignored; kept for thin legacy API wrappers only.
        preempt: bool = False,
        replace_goal: bool = False,
    ) -> Iterator[str]:
        """
        Unified message handler for existing tasks.

        Yields SSE events (same event names as legacy endpoints).
        """
        _ = (preempt, replace_goal)
        stored = get_state_store().load(task_id)
        if not stored:
            payload = merge_stripped_message_payload(
                None,
                text=text,
                meta=meta,
                confirm=confirm,
                intervention=intervention,
                priority=priority,
            )
            payload = stamp_session_mode(payload)
            yield from self.handle_new_task(
                user_id=user_id,
                task_type=task_type,
                input_payload=payload,
                session_id=task_id,
                new_session=new_session or bool((meta or {}).get("new_session")),
                client_message_id=client_message_id,
            )
            return

        stored = sync_fsm_state(stored)
        if _check_idempotent(stored, client_message_id):
            import json

            yield f"event: done\ndata: {json.dumps({'task_id': task_id, 'status': stored.get('status'), 'idempotent': True}, ensure_ascii=False)}\n\n"
            return

        payload = merge_stripped_message_payload(
            stored.get("input_payload"),
            text=text,
            meta=meta,
            confirm=confirm,
            intervention=intervention,
            priority=priority,
        )
        if client_message_id:
            payload = {**payload, "client_message_id": str(client_message_id)}
        inbound, event = _resolve_inbound_dispatch(stored, payload)

        if event.event_type in ("redirect", "interrupt"):
            stored = RunController.cancel(stored, reason=f"user_{event.event_type}")
            get_state_store().save(stored)
            yield from self._dispatch_redirect(
                task_id,
                text,
                intervention=intervention,
                confirm=confirm,
                priority=priority,
            )
        elif event.event_type == "resume":
            yield from self._runner.stream_resume_mission(task_id, confirm=confirm)
        elif event.event_type == "confirm":
            yield from self._runner.stream_resume_mission(task_id, confirm=True)
        elif event.event_type == "status_query":
            yield from self._dispatch_status_query(task_id, text, stored)
        else:
            yield from self._dispatch_new_turn(
                task_id,
                text,
                payload=inbound,
                user_id=user_id,
                task_type=task_type,
                new_session=new_session,
            )

        if client_message_id:
            final = get_state_store().load(task_id)
            if final:
                get_state_store().save(_stamp_applied(final, client_message_id))

    def handle_new_task(
        self,
        *,
        user_id: str,
        task_type: str,
        input_payload: dict[str, Any],
        session_id: Optional[str] = None,
        new_session: bool = False,
        client_message_id: Optional[str] = None,
    ) -> Iterator[str]:
        """New task / session turn via unified classify path."""
        payload = stamp_session_mode(dict(input_payload or {}))
        if session_id:
            stored = get_state_store().load(session_id)
            if stored and _check_idempotent(stored, client_message_id):
                import json

                yield f"event: done\ndata: {json.dumps({'task_id': session_id, 'status': stored.get('status'), 'idempotent': True}, ensure_ascii=False)}\n\n"
                return
        yield from self._runner.stream_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
            session_id=session_id,
            new_session=new_session,
        )
        if session_id and client_message_id:
            final = get_state_store().load(session_id)
            if final:
                get_state_store().save(_stamp_applied(final, client_message_id))

    def _dispatch_redirect(
        self,
        task_id: str,
        text: str,
        *,
        intervention: Optional[dict[str, Any]] = None,
        confirm: bool = False,
        priority: int = 0,
    ) -> Iterator[str]:
        """Redirect = steer + supersede replan stream (single SSE)."""
        yield from self._runner.stream_steer_mission(
            task_id,
            text,
            intervention=intervention,
            confirm=confirm,
            priority=priority,
        )

    def _dispatch_new_turn(
        self,
        task_id: str,
        text: str,
        *,
        payload: dict[str, Any],
        user_id: str,
        task_type: str,
        new_session: bool,
    ) -> Iterator[str]:
        """Continue session with new/clarification turn."""
        yield from self._runner.stream_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
            session_id=task_id,
            new_session=new_session,
        )

    def _dispatch_status_query(
        self,
        task_id: str,
        text: str,
        state: AgentState,
    ) -> Iterator[str]:
        """Status inquiry: steer with forced pause intervention."""
        yield from self._runner.stream_steer_mission(
            task_id,
            text,
            intervention={"action": "pause", "force": True, "reason": "status inquiry"},
            priority=100,
        )


_controller: Optional[SessionController] = None


def get_session_controller() -> SessionController:
    global _controller
    if _controller is None:
        _controller = SessionController()
    return _controller


def handle_message(task_id: str, text: str, **kwargs: Any) -> Iterator[str]:
    return get_session_controller().handle_message(task_id, text, **kwargs)


def run_message_sync(task_id: str, text: str, **kwargs: Any) -> AgentState:
    """Consume unified message stream and return final persisted state."""
    for _ in handle_message(task_id, text, **kwargs):
        pass
    stored = get_state_store().load(task_id)
    if not stored:
        raise KeyError(f"Task not found: {task_id}")
    return stored
