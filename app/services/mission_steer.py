"""
Mission steer — queue or apply user input without NLP inference.

Optional structured `intervention` = forced override; plain `message` = next planning turn only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.mission_intervention import (
    _normalize_intervention,
    apply_intervention_to_payload,
    intervention_from_payload,
)
from app.services.mission_orchestrator import (
    insert_work_item_after_current,
    orchestration_enabled,
    work_plan_completed,
)
from app.services.state_store import get_state_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def steer_requires_planning(payload: dict[str, Any]) -> bool:
    """True until the planning LLM has run once after user steer."""
    return bool(payload.get("require_planning_after_steer")) and not payload.get(
        "steer_planning_done"
    )


def steer_needs_planning_llm(
    *,
    message: str = "",
    intervention: Optional[dict[str, Any]] = None,
) -> bool:
    """Whether steer must go through planning (not mechanical step_policy only)."""
    if (message or "").strip():
        return True
    if not intervention:
        return False
    if intervention.get("use_planning"):
        return True
    if intervention.get("force"):
        return False
    return True


def apply_steer_planning_gate(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["require_planning_after_steer"] = True
    out["steer_planning_done"] = False
    out.pop("skip_planning_llm", None)
    return out


def complete_steer_planning(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["steer_planning_done"] = True
    out["require_planning_after_steer"] = False
    return out


def mission_must_run_planning(state: AgentState) -> bool:
    """Block subgraph:writing until planning re-interprets steer / new goal."""
    payload = state.get("input_payload") or {}
    if steer_requires_planning(payload):
        return True
    if payload.get("steer_applied_at") and not intervention_from_payload(payload):
        return True
    return False


def goal_requests_outline_read(goal: str) -> bool:
    """
    Session-turn read-only route: user asks to inspect existing outline (not steer NLP).
    Maps to review_outline intervention (read_text_artifact), not outcome confirmation.
    """
    text = (goal or "").strip()
    if not text:
        return False
    lower = text.lower()
    wants_read = any(
        token in text
        for token in ("查看", "检阅", "阅读", "看看", "显示", "展示", "查阅")
    ) or any(token in lower for token in ("read", "inspect", "view", "show"))
    mentions_outline = "大纲" in text or "outline" in lower
    return wants_read and mentions_outline


def review_outline_requested(payload: dict[str, Any]) -> bool:
    """True when explicit intervention or flag requests read-only outline (no regex)."""
    if payload.get("steer_review_outline"):
        return True
    block = intervention_from_payload(payload)
    return bool(block and block.get("action") == "review_outline")


def apply_review_outline_mode(
    payload: dict[str, Any],
    mission: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Route steer to read outline artifact, not novel body streaming."""
    from app.config.settings import settings
    from app.domain.mission import StepPolicy

    mission = mission or {}
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    outline_name = str(policy.outline_artifact or "outline.txt")
    out = dict(payload)
    out["steer_review_outline"] = True
    out["writing_intent"] = {
        "enabled": False,
        "action": "review_outline",
        "source": "steer_review",
    }
    out["selected_tools"] = ["read_text_artifact"]
    out.setdefault("tool_params", {})
    out["tool_params"]["read_text_artifact"] = {
        "filename": outline_name,
        "max_chars": int(getattr(settings, "MISSION_OUTLINE_MAX_CHARS", 12000)),
    }
    out.pop("skip_planning_llm", None)
    return out


def _append_steer_goal(payload: dict[str, Any], text: str) -> dict[str, Any]:
    """Append steer text to goal instead of replacing prior instructions."""
    text = (text or "").strip()
    if not text:
        return payload
    prev = str(payload.get("goal") or "").strip()
    if not prev:
        payload["goal"] = text
    elif text in prev:
        payload["goal"] = prev
    else:
        payload["goal"] = f"{prev}\n\n[steer] {text}"
    return payload


def normalize_pending_entries(pending: Any) -> list[dict[str, Any]]:
    """Legacy single dict or {messages: [...]} → list of queued steer entries."""
    if not pending:
        return []
    if isinstance(pending, list):
        return [e for e in pending if isinstance(e, dict)]
    if isinstance(pending, dict):
        raw_messages = pending.get("messages")
        if isinstance(raw_messages, list) and raw_messages:
            return [e for e in raw_messages if isinstance(e, dict)]
        if pending.get("message") or pending.get("intervention"):
            return [
                {
                    "message": str(pending.get("message") or pending.get("content") or "").strip(),
                    "intervention": pending.get("intervention"),
                    "queued_at": pending.get("queued_at") or _now_iso(),
                }
            ]
    if isinstance(pending, str) and pending.strip():
        return [{"message": pending.strip(), "queued_at": _now_iso()}]
    return []


def pending_steer_is_set(pending: Any) -> bool:
    return bool(normalize_pending_entries(pending))


def build_pending_queue(
    existing: Any,
    *,
    message: str = "",
    intervention: Optional[dict[str, Any]] = None,
    priority: int = 0,
    preempt: bool = False,
) -> dict[str, Any]:
    """Merge a new steer into the pending queue (preserves prior queued messages)."""
    entries = list(normalize_pending_entries(existing))
    entry: dict[str, Any] = {"queued_at": _now_iso()}
    if message.strip():
        entry["message"] = message.strip()
    if intervention:
        entry["intervention"] = intervention
    if int(priority or 0) > 0:
        entry["priority"] = int(priority)
    if preempt:
        entry["preempt"] = True
    if entry.get("message") or entry.get("intervention"):
        entries.append(entry)
    combined = "\n\n".join(
        str(e.get("message") or "").strip() for e in entries if e.get("message")
    ).strip()
    return {
        "queued_at": (entries[0].get("queued_at") if entries else _now_iso()),
        "messages": entries,
        "message": combined,
    }


def pending_steer_priority(pending: Any) -> int:
    """Max priority among queued steer entries (0 when absent)."""
    entries = normalize_pending_entries(pending)
    if not entries:
        return 0
    best = 0
    for e in entries:
        try:
            best = max(best, int(e.get("priority") or 0))
        except (TypeError, ValueError):
            continue
    return best


def pending_steer_preempt(pending: Any) -> bool:
    """Whether any queued steer requested preemption (best-effort)."""
    return any(bool(e.get("preempt")) for e in normalize_pending_entries(pending))


def apply_steer_message(
    state: AgentState,
    message: str = "",
    *,
    messages: Optional[list[str]] = None,
    intervention: Optional[dict[str, Any]] = None,
    source: str = "user",
    confirm: bool = False,
) -> AgentState:
    """
    Merge steer input. Structured intervention with force=true overrides the loop;
    message-only steer defers to planning LLM + artifact tools on the next turn.
    """
    payload = dict(state.get("input_payload") or {})
    history = list(state.get("conversation_history") or payload.get("conversation_history") or [])

    texts: list[str] = [t.strip() for t in (messages or []) if (t or "").strip()]
    if (message or "").strip():
        texts.append(message.strip())

    norm: Optional[dict[str, Any]] = None
    if intervention:
        norm = _normalize_intervention(intervention)
    elif texts:
        existing = intervention_from_payload(payload)
        if existing and existing.get("force"):
            norm = _normalize_intervention(existing)

    for text in texts:
        history.append({"role": "user", "content": text, "steer": True, "at": _now_iso()})
        payload = _append_steer_goal(payload, text)

    if norm:
        payload = apply_intervention_to_payload(payload, norm)
    elif texts:
        payload["conversation_history"] = history
        payload.pop("skip_planning_llm", None)

    combined = "\n".join(texts)

    from app.services.mission_steer_confirm import clear_steer_confirmation_flags
    from app.services.mission_steer_outcome_confirm import clear_steer_outcome_flags
    from app.services.steer_confirmation_actions import try_apply_structured_confirm

    if confirm:
        confirmed = try_apply_structured_confirm(state, confirm=True)
        if confirmed is not None:
            get_state_store().save(confirmed)
            return confirmed
        if not texts and not norm:
            raise ValueError(
                "confirm=true but no pending steer confirmation "
                "(intent or outcome gate)"
            )

    if steer_needs_planning_llm(message=combined, intervention=norm):
        payload = apply_steer_planning_gate(payload)

    payload.pop("steer_intent_confirmed", None)
    payload.pop("steer_intent_confirmed_at", None)
    payload = clear_steer_confirmation_flags(payload)
    payload = clear_steer_outcome_flags(payload)
    payload.pop("steer_outcome_confirmed_for", None)
    payload.pop("steer_outcome_confirmed_at", None)
    payload.pop("steer_outcome_confirmed_batches", None)
    if norm and str((norm or {}).get("action") or "") in (
        "rewrite_outline",
        "reset_body",
        "edit_plot",
    ):
        payload["steer_watch_outcome"] = True

    updated = merge_state(
        state,
        input_payload=payload,
        conversation_history=history,
        pending_user_message=None,
        steer_applied_at=_now_iso(),
        audit_log=append_audit(
            state,
            "steer",
            "applied",
            {
                "source": source,
                "forced": bool(norm and norm.get("force")),
                "action": (norm or {}).get("action"),
                "message_count": len(texts),
                "steer_review_outline": bool(payload.get("steer_review_outline")),
            },
        ),
    )

    if norm and not norm.get("force") and norm.get("work_item") and orchestration_enabled(updated.get("mission") or {}):
        wi = norm["work_item"]
        updated = insert_work_item_after_current(
            updated,
            {
                "id": str(wi.get("id") or f"wi-steer-{_now_iso()}"),
                "kind": str(wi.get("kind") or norm.get("action", "custom")),
                "title": str(wi.get("title") or "steer work item"),
                "status": "pending",
                "params": dict(wi.get("params") or {}),
            },
        )
    elif norm and not norm.get("force") and norm.get("action") == "edit_plot" and orchestration_enabled(
        updated.get("mission") or {}
    ):
        updated = insert_work_item_after_current(
            updated,
            {
                "id": f"wi-edit-{_now_iso()}",
                "kind": "edit_plot",
                "title": "edit_plot",
                "status": "pending",
                "params": {"edit_spec": norm.get("edit_spec") or {}},
            },
        )

    get_state_store().save(updated)
    return updated


def has_pending_steer(task_id: str) -> bool:
    """True when a steer message is queued for the next step boundary."""
    stored = get_state_store().load(task_id)
    return bool(stored and pending_steer_is_set(stored.get("pending_user_message")))


def consume_pending_steer(state: AgentState) -> AgentState:
    pending = state.get("pending_user_message")
    if not pending_steer_is_set(pending):
        stored = get_state_store().load(state["task_id"])
        if stored:
            pending = stored.get("pending_user_message")
    entries = normalize_pending_entries(pending)
    if not entries:
        return state

    texts = [str(e.get("message") or "").strip() for e in entries if e.get("message")]
    intervention: Optional[dict[str, Any]] = None
    for entry in reversed(entries):
        if entry.get("intervention"):
            intervention = entry.get("intervention")
            break

    return apply_steer_message(
        state,
        messages=texts,
        intervention=intervention,
        source="pending",
    )


def queue_steer_message(
    task_id: str,
    message: str = "",
    *,
    intervention: Optional[dict[str, Any]] = None,
    confirm: bool = False,
    priority: int = 0,
    preempt: bool = False,
) -> AgentState:
    if not (message or "").strip() and not intervention and not confirm:
        raise ValueError("steer requires message, intervention, and/or confirm=true")

    stored = get_state_store().load(task_id)
    if not stored:
        raise KeyError(f"Task not found: {task_id}")
    status = str(stored.get("status", ""))

    if status in (TaskStatus.MISSION_PAUSED.value, TaskStatus.REASONED.value):
        return apply_steer_message(
            stored,
            message,
            intervention=intervention,
            confirm=confirm,
        )

    if status not in (
        TaskStatus.MISSION_RUNNING.value,
        TaskStatus.MISSION_PAUSED.value,
        TaskStatus.WAITING_REVIEW.value,
        TaskStatus.REASONED.value,
    ):
        mission = stored.get("mission") or {}
        incomplete_orchestration = bool(
            stored.get("mission")
            and orchestration_enabled(mission)
            and not work_plan_completed(stored)
        )
        if not incomplete_orchestration:
            if status in (TaskStatus.COMPLETED.value, TaskStatus.NEW.value):
                raise ValueError(f"Task {task_id} cannot accept steer in status {status}")

    pending = build_pending_queue(
        stored.get("pending_user_message"),
        message=message,
        intervention=intervention,
        priority=priority,
        preempt=preempt,
    )

    updated = merge_state(
        stored,
        pending_user_message=pending,
        audit_log=append_audit(
            stored,
            "steer",
            "queued",
            {
                "has_intervention": bool(intervention),
                "queue_depth": len(pending.get("messages") or []),
            },
        ),
    )
    get_state_store().save(updated)
    return updated
