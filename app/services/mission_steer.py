"""
Mission steer：长任务中途纠偏。

入口：task_api.steer_task → graph_runner.steer_mission → queue_steer_message；
续聊 session_turn；边界 mission_decide/mission_act 前 consume_pending_steer。
queue_steer_message：PAUSED/REASONED 立即 apply；RUNNING 默认排队，foreground preempt 时立即消费并进入重规划暂停。
apply_steer_message：合并文本与 intervention，可选 confirm 与 planning_gate。

Mid-mission steer via API queue or immediate apply; integrates with planning and confirmation gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.mission_intervention import (
    apply_intervention_to_payload,
    intervention_from_payload,
)
from app.services.mission_orchestrator import (
    insert_work_item_after_current,
    orchestration_enabled,
    work_plan_completed,
)
from app.services.state_store import get_state_store
from app.services.mission_execution import PAUSE_FORCED


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def steer_replan_planning_satisfied(payload: dict[str, Any]) -> bool:
    """Steer replan already produced a turn_contract — block redundant planning passes."""
    if not payload.get("steer_planning_done"):
        return False
    if payload.get("require_planning_after_steer") or payload.get("foreground_replan_dispatch"):
        return False
    from app.services.turn_contract import contract_from_payload

    return contract_from_payload(payload) is not None


def steer_requires_planning(payload: dict[str, Any]) -> bool:
    """True until planning runs after steer or after contract invalidation."""
    from app.services.turn_contract_lifecycle import contract_replan_required

    if steer_replan_planning_satisfied(payload):
        return False
    if contract_replan_required(payload):
        return True
    return bool(payload.get("require_planning_after_steer")) and not payload.get(
        "steer_planning_done"
    )


def planning_steer_replan_active(
    payload: dict[str, Any],
    state: AgentState | dict[str, Any] | None = None,
) -> bool:
    """True when planning must interpret latest_steer_message (not mechanical resume)."""
    if steer_requires_planning(payload):
        return True
    if payload.get("foreground_replan_dispatch"):
        return True
    steer = str(payload.get("latest_steer_message") or "").strip()
    if steer and payload.get("steer_applied_at") and not payload.get("steer_planning_done"):
        return True
    if state is not None:
        from app.services.mission_supersede import is_supersede_replan_pending

        if is_supersede_replan_pending(payload, state):  # type: ignore[arg-type]
            return True
    return False


def steer_needs_planning_llm(
    *,
    message: str = "",
    intervention: Optional[dict[str, Any]] = None,
) -> bool:
    """Whether steer must go through planning (not mechanical step_policy only)."""
    from app.services.interaction_goal import goal_is_mission_status_query
    from app.services.manuscript_service import is_continue_writing_goal

    if goal_is_mission_status_query(message):
        return False
    if is_continue_writing_goal(message):
        return False
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
    from app.services.turn_contract_lifecycle import (
        REASON_STEER,
        invalidate_turn_contract_payload,
    )
    from app.services.turn_kind import stamp_turn_kind

    out = invalidate_turn_contract_payload(dict(payload), REASON_STEER)
    out["require_planning_after_steer"] = True
    out["steer_planning_done"] = False
    out.pop("skip_planning_llm", None)
    if not out.get("steer_applied_at"):
        out["steer_applied_at"] = _now_iso()
    return stamp_turn_kind(out, "steer_replan")


def complete_steer_planning(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.turn_contract_lifecycle import clear_contract_replan_requirement
    from app.services.turn_kind import stamp_turn_kind

    out = clear_contract_replan_requirement(dict(payload))
    out["steer_planning_done"] = True
    out["require_planning_after_steer"] = False
    from app.services.mission_oma.intent_spec import intent_spec_from_payload, normalize_intent_spec

    out["intent_spec"] = normalize_intent_spec(intent_spec_from_payload(out)).to_dict()
    return stamp_turn_kind(out, "steer_execute")


def mission_must_run_planning(state: AgentState) -> bool:
    """在 planning 重新解释 steer 前，阻塞 subgraph:writing。

    Block writing subgraph until planning re-interprets steer.
    Only ``steer_requires_planning`` (gate + steer_planning_done) — do not key on
    ``steer_applied_at`` alone or every post-steer turn replans forever.
    """
    payload = state.get("input_payload") or {}
    return steer_requires_planning(payload)


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
    *,
    intent: Optional[Any] = None,
    intervention: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Route steer to read outline artifact via WritingCommand."""
    from app.domain.writing_intent_model import WritingIntentRecord
    from app.services.mission_intervention import normalize_payload_execution_fields
    from app.services.writing.command_builder import build_from_intent
    from app.services.writing.intent_parser import parse_intent_from_intervention
    from app.services.writing.state_machine import enqueue_command

    mission = mission or {}
    out = normalize_payload_execution_fields(dict(payload))
    out["steer_review_outline"] = True
    record = intent
    if record is None and intervention:
        record = parse_intent_from_intervention(intervention, source="review_outline")
    if record is None:
        record = WritingIntentRecord(action="review_outline", force=False, source="steer_review")
    command = build_from_intent(
        {"input_payload": out, "mission": mission, "manuscript": out.get("manuscript") or {}},
        record,
    )
    out = enqueue_command(out, command)
    out.pop("skip_planning_llm", None)
    return out


def _append_steer_goal(
    payload: dict[str, Any],
    text: str,
    *,
    replace_goal: bool = False,
) -> dict[str, Any]:
    """Append steer text by default; optionally replace current goal."""
    text = (text or "").strip()
    if not text:
        return payload
    if replace_goal:
        payload["goal"] = text
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
    replace_goal: bool = False,
    goal_staged: bool = False,
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
    if replace_goal:
        entry["replace_goal"] = True
    if goal_staged:
        entry["goal_staged"] = True
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


def pending_has_forced_action(pending: Any, action: str) -> bool:
    """Check queued entries for a forced intervention action."""
    target = str(action or "").strip()
    if not target:
        return False
    for entry in normalize_pending_entries(pending):
        itv = entry.get("intervention")
        if not isinstance(itv, dict):
            continue
        if str(itv.get("action") or "") == target and bool(itv.get("force")):
            return True
    return False


def apply_steer_message(
    state: AgentState,
    message: str = "",
    *,
    messages: Optional[list[str]] = None,
    intervention: Optional[dict[str, Any]] = None,
    source: str = "user",
    confirm: bool = False,
    replace_goal: bool = False,
    skip_history_append: bool = False,
    persist: bool = True,
) -> AgentState:
    """
    合并 steer 到 state（立即生效路径）；force intervention 可绕过 planning。

    Merge steer into state; message-only steers set planning gate for next turn.
    ``skip_history_append``: caller already appended user lines (e.g. session_turn).
    ``persist=False``: do not write state store (prepare_session_turn builds fresh turn).
    """
    payload = dict(state.get("input_payload") or {})
    history = list(state.get("conversation_history") or payload.get("conversation_history") or [])

    texts: list[str] = [t.strip() for t in (messages or []) if (t or "").strip()]
    if (message or "").strip():
        texts.append(message.strip())

    norm: Optional[dict[str, Any]] = None
    if intervention:
        norm = dict(intervention)
    elif texts:
        existing = intervention_from_payload(payload)
        if existing and existing.get("force"):
            norm = dict(existing)

    for text in texts:
        if not skip_history_append:
            history.append({"role": "user", "content": text, "steer": True, "at": _now_iso()})
        payload = _append_steer_goal(payload, text, replace_goal=replace_goal)
        if text.strip():
            payload["latest_steer_message"] = text.strip()

    if norm:
        payload = apply_intervention_to_payload(payload, norm)
    elif texts:
        payload["conversation_history"] = history
        payload.pop("skip_planning_llm", None)

    combined = "\n".join(texts)

    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.steer_confirmation_actions import (
        is_proceed_confirm_message,
        try_apply_structured_confirm,
    )

    if texts and steer_confirmation_pending(dict(payload)):
        last = texts[-1]
        if is_proceed_confirm_message(last):
            confirmed = try_apply_structured_confirm(state, confirm=True)
            if confirmed is not None:
                if persist:
                    get_state_store().save(confirmed)
                return confirmed

    from app.services.mission_steer_confirm import clear_steer_confirmation_flags
    from app.services.mission_steer_outcome_confirm import clear_steer_outcome_flags

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

    from app.services.mission_execution import has_execution_grant
    from app.services.interaction_goal import goal_is_mission_status_query

    status_inquiry = bool(payload.get("mission_status_inquiry")) or any(
        goal_is_mission_status_query(t) for t in texts
    )
    if status_inquiry:
        payload["mission_status_inquiry"] = True
        if not norm:
            norm = {"action": "pause", "force": True, "reason": "status inquiry"}
            payload = apply_intervention_to_payload(payload, norm)
        payload["writing_intent"] = {
            "enabled": False,
            "source": "status_inquiry",
            "blocked_by": "status_inquiry",
        }
        payload["steer_planning_done"] = True
        payload.pop("require_planning_after_steer", None)
        payload.pop("execution_grant", None)
        payload.pop("current_work_item", None)
    elif steer_needs_planning_llm(message=combined, intervention=norm):
        if has_execution_grant(payload):
            from app.services.intent_composer import record_grant_steer_conflict

            payload = record_grant_steer_conflict(
                payload, reason="steer_overrides_mechanical_grant"
            )
        payload = apply_steer_planning_gate(payload)
        payload["writing_intent"] = {
            "enabled": False,
            "source": "await_steer_planning",
        }
        payload.pop("current_work_item", None)
        payload["skip_planning_llm"] = False

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

    if norm and norm.get("work_item") and orchestration_enabled(updated.get("mission") or {}):
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
    elif norm and norm.get("action") == "edit_plot" and orchestration_enabled(
        updated.get("mission") or {}
    ):
        updated = insert_work_item_after_current(
            updated,
            {
                "id": f"wi-edit-{_now_iso()}",
                "kind": "edit_plot",
                "title": "edit_plot",
                "status": "pending",
                "params": {},
            },
        )

    if persist and steer_requires_planning(dict(updated.get("input_payload") or {})):
        if source != "pending":
            from app.services.mission_supersede import finalize_steer_for_supersede_replan

            updated = finalize_steer_for_supersede_replan(
                updated,
                source=source,
                steer_text=combined,
            )
            if str(updated.get("status") or "") in (
                TaskStatus.REJECTED.value,
                TaskStatus.FAILED.value,
            ):
                updated = merge_state(
                    updated,
                    status=TaskStatus.MISSION_PAUSED.value,
                    mission_control=None,
                )
    if persist:
        get_state_store().save(updated)
    return updated


def has_pending_steer(task_id: str) -> bool:
    """True when a steer message is queued for the next step boundary."""
    stored = get_state_store().load(task_id)
    return bool(stored and pending_steer_is_set(stored.get("pending_user_message")))


def finalize_preempt_steer_replan(state: AgentState) -> AgentState:
    """Consume queued steer after preempt; queue supersede replan (not resume)."""
    from app.services.execution_control import CONTROL_REPLANNING
    from app.services.mission_supersede import mark_supersede_replan_queued

    updated = consume_pending_steer(state)
    ctx = updated.get("interrupt_context") or {}
    payload = updated.get("input_payload") or {}
    if str(ctx.get("control_state") or "") != CONTROL_REPLANNING and payload.get(
        "require_planning_after_steer"
    ):
        from app.services.foreground_execution import enter_replanning_state

        hint = str(payload.get("steer_action_hint") or payload.get("steer_replan_mode") or "rewrite")
        updated = enter_replanning_state(updated, action_hint=hint)
        ctx = updated.get("interrupt_context") or {}
    if str(ctx.get("control_state") or "") == CONTROL_REPLANNING:
        updated = mark_supersede_replan_queued(updated, source="steer_preempt")
    get_state_store().save(updated)
    return updated


def consume_pending_steer(state: AgentState) -> AgentState:
    """在 Mission 步边界消费 pending_user_message。

    After foreground preempt, enter REPLANNING — no mechanical append on stale plan.
    """
    pending = state.get("pending_user_message")
    if not pending_steer_is_set(pending):
        stored = get_state_store().load(state["task_id"])
        if stored:
            pending = stored.get("pending_user_message")
    entries = normalize_pending_entries(pending)
    if not entries:
        return state

    texts = [str(e.get("message") or "").strip() for e in entries if e.get("message")]
    payload_check = dict(state.get("input_payload") or {})
    combined_early = "\n".join(t for t in texts if t).strip()
    if (
        payload_check.get("steer_planning_done")
        and combined_early
        and combined_early == str(payload_check.get("latest_steer_message") or "").strip()
    ):
        return merge_state(state, pending_user_message=None)
    intervention: Optional[dict[str, Any]] = None
    replace_goal = False
    for entry in reversed(entries):
        if entry.get("intervention"):
            intervention = entry.get("intervention")
            break
    for entry in reversed(entries):
        if entry.get("replace_goal"):
            replace_goal = True
            break

    from app.services.foreground_execution import (
        enter_replanning_state,
        extract_writing_constraints,
        foreground_preempt_active,
        resolve_pending_steer_action_hint,
    )

    payload_before = dict(state.get("input_payload") or {})
    preempted = foreground_preempt_active(state) or bool(
        payload_before.get("foreground_preempt_pending")
    )
    action_hint = resolve_pending_steer_action_hint(
        entries,
        payload_hint=str(payload_before.get("steer_action_hint") or ""),
    )
    if preempted and action_hint == "append":
        action_hint = "repair"
    constraints = extract_writing_constraints(texts)
    skip_history = bool(texts) and all(
        bool(e.get("goal_staged"))
        for e in entries
        if str(e.get("message") or "").strip()
    )

    if texts:
        payload_before = dict(payload_before)
        payload_before["latest_steer_message"] = "\n".join(texts).strip()
        state = merge_state(state, input_payload=payload_before)

    updated = apply_steer_message(
        state,
        messages=texts,
        intervention=intervention,
        source="pending",
        replace_goal=replace_goal,
        skip_history_append=skip_history,
    )

    if preempted or action_hint in ("rewrite", "repair", "cancel_only"):
        updated = enter_replanning_state(
            updated,
            action_hint=action_hint,
            constraints=constraints,
        )
    elif constraints:
        from app.services.foreground_execution import merge_writing_constraints

        updated = merge_state(
            updated,
            input_payload=merge_writing_constraints(
                dict(updated.get("input_payload") or {}),
                constraints,
            ),
        )
    return updated


def queue_steer_message(
    task_id: str,
    message: str = "",
    *,
    intervention: Optional[dict[str, Any]] = None,
    confirm: bool = False,
    priority: int = 0,
    preempt: bool = False,
    replace_goal: bool = False,
) -> AgentState:
    """
    Steer API：按任务状态排队或立即 apply_steer_message。

    Queue when MISSION_RUNNING; immediate apply when paused.
    """
    if not (message or "").strip() and not intervention and not confirm:
        raise ValueError("steer requires message, intervention, and/or confirm=true")

    from app.services.foreground_execution import (
        INTERRUPT_P0,
        classify_steer_interrupt,
        steer_action_hint,
        trigger_foreground_preempt,
    )
    from app.services.interaction_goal import goal_is_mission_status_query

    msg_text = (message or "").strip()
    interrupt_tier = classify_steer_interrupt(
        msg_text,
        intervention=intervention,
        priority=priority,
        preempt=preempt,
    )
    if interrupt_tier == INTERRUPT_P0:
        preempt = True
        priority = max(int(priority or 0), 100)
    elif interrupt_tier == 1:
        preempt = preempt or True
        priority = max(int(priority or 0), 50)

    if msg_text and goal_is_mission_status_query(msg_text):
        replace_goal = False
        preempt = True
        priority = max(int(priority or 0), 100)
        interrupt_tier = INTERRUPT_P0
        if not intervention:
            intervention = {
                "action": "pause",
                "force": True,
                "reason": "status inquiry",
            }

    from app.services.mission_worker_lost import reconcile_worker_lost

    stored = get_state_store().load(task_id)
    if not stored:
        raise KeyError(f"Task not found: {task_id}")
    stored = reconcile_worker_lost(stored)
    status = str(stored.get("status", ""))
    is_forced_pause = bool(
        intervention
        and str(intervention.get("action") or "") == "pause"
        and bool(intervention.get("force"))
    )

    if status == TaskStatus.COMPLETED.value and (
        (message or "").strip() or intervention or confirm
    ):
        from app.services.session_turn import _reset_execution_fields
        from app.services.session.turn_policy import (
            apply_qa_turn_isolation,
            resolve_session_turn,
        )

        updated = apply_steer_message(
            stored,
            message,
            intervention=intervention,
            confirm=confirm,
            replace_goal=replace_goal,
            source="steer_after_complete",
        )
        merged = dict(updated.get("input_payload") or {})
        goal = str(merged.get("goal") or message or "").strip()
        if goal:
            decision = resolve_session_turn(updated, merged, goal)
            if decision.intent == "isolate_qa":
                merged = apply_qa_turn_isolation(merged, updated)
        updated = _reset_execution_fields(updated, merged)
        if merged.get("mission_suspended"):
            updated = merge_state(
                updated,
                mission=None,
                execution_mode="single",
            )
        get_state_store().save(updated)
        return updated

    if (
        stored.get("mission")
        and not is_forced_pause
        and status
        in (
            TaskStatus.MISSION_PAUSED.value,
            TaskStatus.REASONED.value,
            TaskStatus.REJECTED.value,
            TaskStatus.FAILED.value,
            TaskStatus.PLANNED.value,
            TaskStatus.POLICY_CHECKED.value,
        )
    ):
        return apply_steer_message(
            stored,
            message,
            intervention=intervention,
            confirm=confirm,
            replace_goal=replace_goal,
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

    if is_forced_pause:
        # Emergency stop lane: replace queued steers to avoid starvation behind queue tail.
        norm_pause = dict(intervention or {})
        pending = build_pending_queue(
            None,
            message=message,
            intervention=norm_pause,
            priority=max(100, int(priority or 0)),
            preempt=True,
            replace_goal=False,
        )
        payload_now = apply_intervention_to_payload(
            dict(stored.get("input_payload") or {}),
            norm_pause,
        )
    else:
        payload_now = dict(stored.get("input_payload") or {})
        stage_goal = bool(msg_text)
        if stage_goal:
            from app.services.foreground_execution import (
                extract_writing_constraints,
                merge_writing_constraints,
            )
            from app.services.turn_contract_lifecycle import (
                REASON_STEER,
                invalidate_turn_contract_payload,
            )

            payload_now = _append_steer_goal(
                payload_now,
                msg_text,
                replace_goal=replace_goal,
            )
            payload_now["latest_steer_message"] = msg_text
            payload_now = merge_writing_constraints(
                payload_now,
                extract_writing_constraints([msg_text]),
            )
            payload_now = invalidate_turn_contract_payload(payload_now, REASON_STEER)
            if not payload_now.get("steer_applied_at"):
                payload_now["steer_applied_at"] = _now_iso()
            if steer_needs_planning_llm(message=msg_text, intervention=intervention):
                payload_now = apply_steer_planning_gate(payload_now)
                payload_now["skip_planning_llm"] = False
        pending = build_pending_queue(
            stored.get("pending_user_message"),
            message=message,
            intervention=intervention,
            priority=priority,
            preempt=preempt,
            replace_goal=replace_goal,
            goal_staged=stage_goal,
        )

    updated = merge_state(
        stored,
        input_payload=payload_now,
        pending_user_message=pending,
        status=TaskStatus.MISSION_PAUSED.value if is_forced_pause else stored.get("status"),
        mission_control={
            "done": True,
            "action": "pause",
            "reason": "forced pause requested",
            "pause_reason": PAUSE_FORCED,
        }
        if is_forced_pause
        else stored.get("mission_control"),
        audit_log=append_audit(
            stored,
            "steer",
            "queued",
            {
                "has_intervention": bool(intervention),
                "queue_depth": len(pending.get("messages") or []),
                "interrupt_tier": interrupt_tier,
                "steer_action_hint": steer_action_hint(
                    interrupt_tier, msg_text, intervention
                ),
            },
        ),
    )
    if (
        str(stored.get("status") or "") == TaskStatus.MISSION_RUNNING.value
        and interrupt_tier <= 1
        and not is_forced_pause
    ):
        updated = trigger_foreground_preempt(
            str(task_id),
            updated,
            reason="steer_preempt",
            tier=interrupt_tier,
        )
        payload_now = dict(updated.get("input_payload") or {})
        payload_now["steer_action_hint"] = steer_action_hint(
            interrupt_tier, msg_text, intervention
        )
        payload_now["foreground_preempt_pending"] = True
        payload_now["require_planning_after_steer"] = True
        payload_now["steer_planning_done"] = False
        updated = merge_state(updated, input_payload=payload_now)
        updated = finalize_preempt_steer_replan(updated)
        return updated
    get_state_store().save(updated)
    return updated
