"""Preemptive foreground execution: epoch + cooperative cancellation for long tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.execution_control import (
    CONTROL_INTERRUPT_REQUESTED,
    CONTROL_REPLANNING,
    CancelRequested,
    ensure_interrupt_context,
    record_control_event,
)

# Steer interrupt tiers (debug.log § interrupt priority)
INTERRUPT_P0 = 0
INTERRUPT_P1 = 1
INTERRUPT_P2 = 2

_P0_STOP_RE = re.compile(
    r"(停止|取消|不要继续|别写了|停下|停掉|重写|按我的新要求|按新要求|方向错了|"
    r"不能出现|不得出现|不允许出现|违规|越权|"
    r"\b(stop|cancel|abort|rewrite|do not continue)\b)",
    re.IGNORECASE,
)
_FORBIDDEN_CLAUSE_RE = re.compile(
    r"(?:不能|不得|不允许)出现[「\"']?([^」\"'；。，\n]+)"
)
_EXPLICIT_FORBIDDEN_NAME_RE = re.compile(
    r"(?:不能|不得|不允许)(?:写|用|提及)?[「\"']?([\u4e00-\u9fffA-Za-z]{2,12})[」\"']?"
)

_P1_SOFT_RE = re.compile(
    r"(调整风格|换风格|补充约束|格式|语气|节奏|篇幅|"
    r"\b(style|format|tone|constraint)\b)",
    re.IGNORECASE,
)
_P0_MATERIAL_RE = re.compile(
    r"(基于原(?:电影|剧|著|小说)|原(?:电影|剧|著|小说)(?:人物|角色)|"
    r"保留.{0,8}人物|人物需要为|只改.{0,12}剧情|改动.{0,12}剧情|剧情走向|"
    r"重写大纲|改大纲|换剧情|剧情不对|方向不对)",
    re.IGNORECASE,
)
_REWRITE_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "reset_body",
        "edit_plot",
        "repair",
        "rewrite",
    }
)


class EpochStale(Exception):
    """Raised when a step/stream belongs to a superseded foreground epoch."""


@dataclass
class CancellationToken:
    """Cooperative cancel handle bound to a foreground epoch."""

    task_id: str
    epoch: int
    step_id: str = ""
    generation_id: str = ""
    cancelled: bool = False

    def mark_cancelled(self) -> None:
        self.cancelled = True

    def is_stale(self, current_epoch: int) -> bool:
        return self.cancelled or int(current_epoch) != int(self.epoch)

    def check(self, *, phase: str = "") -> None:
        if self.cancelled:
            raise CancelRequested(f"foreground cancelled ({phase})")
        from app.services.task_control import snapshot_task_control

        control = snapshot_task_control(self.task_id)
        if control and control.cancel_requested:
            self.cancelled = True
            raise CancelRequested(control.reason or "cancel requested")
        if control and int(control.foreground_epoch or 0) > int(self.epoch):
            self.cancelled = True
            raise EpochStale(f"epoch {self.epoch} stale vs {control.foreground_epoch}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_foreground_epoch(state: AgentState | dict[str, Any]) -> int:
    ctx = ensure_interrupt_context(state) if isinstance(state, dict) and "interrupt_context" in state else {}
    if not ctx and isinstance(state, dict):
        raw = state.get("interrupt_context") or {}
        ctx = raw if isinstance(raw, dict) else {}
    try:
        return max(0, int(ctx.get("foreground_epoch") or 0))
    except (TypeError, ValueError):
        return 0


def bump_foreground_epoch(state: AgentState, *, reason: str = "foreground_preempt") -> AgentState:
    """Invalidate in-flight foreground runs; only the latest epoch may commit."""
    ctx = ensure_interrupt_context(state)
    next_epoch = int(ctx.get("foreground_epoch") or 0) + 1
    ctx["foreground_epoch"] = next_epoch
    ctx["control_state"] = CONTROL_INTERRUPT_REQUESTED
    ctx["last_control_event"] = {
        "event": "foreground_epoch_bumped",
        "at": _now_iso(),
        "detail": {"epoch": next_epoch, "reason": reason},
    }
    updated = merge_state(state, interrupt_context=ctx)
    return record_control_event(
        updated,
        "foreground_epoch_bumped",
        detail={"epoch": next_epoch, "reason": reason},
    )


def bind_step_epoch(state: AgentState, step_meta: dict[str, Any]) -> dict[str, Any]:
    """Attach current foreground epoch to an active step."""
    meta = dict(step_meta)
    meta["foreground_epoch"] = get_foreground_epoch(state)
    return meta


def assert_epoch_valid_for_commit(
    state: AgentState,
    *,
    step_epoch: int | None = None,
    phase: str = "commit",
) -> None:
    """Reject commits from superseded foreground generations."""
    current = get_foreground_epoch(state)
    bound = step_epoch
    if bound is None:
        ctx = ensure_interrupt_context(state)
        active = ctx.get("active_step") if isinstance(ctx.get("active_step"), dict) else {}
        try:
            bound = int(active.get("foreground_epoch") or current)
        except (TypeError, ValueError):
            bound = current
    if int(bound or 0) < current:
        raise EpochStale(f"step epoch {bound} < foreground {current} ({phase})")
    from app.services.task_control import snapshot_task_control

    control = snapshot_task_control(str(state["task_id"]))
    if control and int(control.foreground_epoch or 0) > int(bound or 0):
        raise EpochStale(
            f"task control epoch {control.foreground_epoch} > step {bound} ({phase})"
        )


def classify_steer_interrupt(
    message: str = "",
    *,
    intervention: Optional[dict[str, Any]] = None,
    priority: int = 0,
    preempt: bool = False,
) -> int:
    """
    Classify steer into P0 (force preempt), P1 (soft preempt), P2 (queue).

    P0: stop / rewrite / constraint violation — must cancel current foreground run.
    P1: style/format tweaks — preempt when safe.
    P2: ordinary follow-ups — queue at step boundary.
    """
    if preempt or int(priority or 0) >= 100:
        return INTERRUPT_P0
    if intervention:
        action = str(intervention.get("action") or "")
        if bool(intervention.get("force")):
            return INTERRUPT_P0
        if action in _REWRITE_ACTIONS:
            return INTERRUPT_P0
    text = (message or "").strip()
    if text and _P0_STOP_RE.search(text):
        return INTERRUPT_P0
    if text and _P0_MATERIAL_RE.search(text):
        return INTERRUPT_P0
    if text and _P1_SOFT_RE.search(text):
        return INTERRUPT_P1
    if int(priority or 0) > 0:
        return INTERRUPT_P1
    return INTERRUPT_P2


def steer_action_hint(tier: int, message: str = "", intervention: Optional[dict[str, Any]] = None) -> str:
    """Map interrupt tier to replan hint (append vs rewrite vs cancel-only)."""
    if tier == INTERRUPT_P0:
        if intervention and str(intervention.get("action") or "") in ("pause", "cancel"):
            return "cancel_only"
        if message and _P0_STOP_RE.search(message):
            if any(tok in message for tok in ("停止", "取消", "stop", "cancel")):
                return "cancel_only"
        return "rewrite"
    if tier == INTERRUPT_P1:
        return "repair"
    return "append"


def trigger_foreground_preempt(
    task_id: str,
    state: AgentState,
    *,
    reason: str = "steer_preempt",
    tier: int = INTERRUPT_P0,
) -> AgentState:
    """
    Bump epoch and signal in-process executor to stop streaming/committing.

    Does not apply steer payload — caller still queues/applies for replanning.
    """
    from app.services.task_control import request_pause, sync_task_control_epoch

    updated = bump_foreground_epoch(state, reason=reason)
    epoch = get_foreground_epoch(updated)
    sync_task_control_epoch(str(task_id), epoch)
    request_pause(str(task_id), reason=reason)
    if tier == INTERRUPT_P0:
        from app.services.task_control import request_interrupt_stream

        request_interrupt_stream(str(task_id), reason=reason)
    return merge_state(
        updated,
        audit_log=append_audit(
            updated,
            "foreground",
            "preempt_triggered",
            {"epoch": epoch, "tier": tier, "reason": reason},
        ),
    )


def new_cancellation_token(
    state: AgentState,
    *,
    step_id: str = "",
    generation_id: str = "",
) -> CancellationToken:
    return CancellationToken(
        task_id=str(state["task_id"]),
        epoch=get_foreground_epoch(state),
        step_id=step_id,
        generation_id=generation_id,
    )


def extract_writing_constraints(texts: list[str]) -> list[str]:
    """Pull explicit forbidden-content clauses from steer messages (commit guard + planning)."""
    found: list[str] = []
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        for match in _FORBIDDEN_CLAUSE_RE.finditer(text):
            clause = match.group(1).strip()
            if clause and clause not in found:
                found.append(clause)
        for match in _EXPLICIT_FORBIDDEN_NAME_RE.finditer(text):
            name = match.group(1).strip()
            if name and name not in found:
                found.append(name)
    return found


def validate_commit_guard(content: str, constraints: list[str]) -> tuple[bool, str]:
    """
    Lightweight pre-commit guard for explicit forbidden literals.

    Semantic constraints (e.g. "characters not in source film") are passed to planning;
    only short explicit tokens are checked mechanically here.
    """
    text = (content or "").strip()
    if not text or not constraints:
        return True, "ok"
    for clause in constraints:
        token = (clause or "").strip()
        if not token:
            continue
        if len(token) <= 16 and token in text:
            return False, f"forbidden literal present: {token}"
    return True, "ok"


def _clear_payload_keys(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Clear keys under merge_state shallow input_payload merge (pop alone is insufficient)."""
    out = dict(payload)
    for key in keys:
        out.pop(key, None)
        out[key] = None
    return out


def merge_writing_constraints(
    payload: dict[str, Any],
    constraints: list[str],
) -> dict[str, Any]:
    if not constraints:
        return payload
    out = dict(payload)
    merged = list(out.get("writing_constraints") or [])
    for item in constraints:
        if item and item not in merged:
            merged.append(item)
    out["writing_constraints"] = merged
    return out


def apply_steer_replan_to_payload(
    payload: dict[str, Any],
    action_hint: str,
    *,
    constraints: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Map steer_action_hint to planning / writing_intent (no mechanical append)."""
    from app.services.session_fsm import apply_replan_gate as apply_steer_planning_gate

    hint = str(action_hint or "append").strip() or "append"
    out = merge_writing_constraints(dict(payload), constraints or [])

    if hint == "cancel_only":
        out["writing_intent"] = {
            "enabled": False,
            "source": "cancel_only",
            "blocked_by": "foreground_preempt",
        }
        out["steer_planning_done"] = True
        out.pop("require_planning_after_steer", None)
        out["require_planning_after_steer"] = None
        out = _clear_payload_keys(out, "execution_grant", "current_work_item")
        out["steer_replan_mode"] = hint
        return out

    if hint in ("rewrite", "repair"):
        out = apply_steer_planning_gate(out)
        out["writing_intent"] = {
            "enabled": False,
            "source": f"await_steer_{hint}",
            "blocked_by": "foreground_replan",
        }
        out = _clear_payload_keys(out, "current_work_item", "execution_grant")
        out["skip_planning_llm"] = False
        out["steer_replan_mode"] = hint
        out["foreground_preempt_consumed"] = True
        if hint == "rewrite":
            out["steer_watch_outcome"] = True
        return out

    return out


def foreground_preempt_active(state: AgentState | dict[str, Any]) -> bool:
    ctx = state.get("interrupt_context") if isinstance(state, dict) else {}
    if not isinstance(ctx, dict):
        ctx = ensure_interrupt_context(state)  # type: ignore[arg-type]
    control = str(ctx.get("control_state") or "")
    return control in (CONTROL_INTERRUPT_REQUESTED, CONTROL_REPLANNING)


def enter_replanning_state(
    state: AgentState,
    *,
    action_hint: str = "rewrite",
    constraints: Optional[list[str]] = None,
) -> AgentState:
    """After preempt + steer consume: block old step commit, force replan path."""
    ctx = ensure_interrupt_context(state)
    ctx["control_state"] = CONTROL_REPLANNING
    ctx["active_step"] = None
    payload = apply_steer_replan_to_payload(
        dict(state.get("input_payload") or {}),
        action_hint,
        constraints=constraints,
    )
    payload.pop("steer_action_hint", None)
    payload.pop("foreground_preempt_pending", None)
    updated = merge_state(state, interrupt_context=ctx, input_payload=payload)
    updated = record_control_event(
        updated,
        "foreground_replanning",
        detail={
            "action_hint": action_hint,
            "constraints": list(constraints or []),
            "epoch": get_foreground_epoch(updated),
        },
    )
    from app.services.session_fsm import FSM_REPLANNING, transition_fsm

    return transition_fsm(updated, FSM_REPLANNING)


def resolve_pending_steer_action_hint(
    entries: list[dict[str, Any]],
    *,
    payload_hint: str = "",
) -> str:
    """Best-effort replan hint from queued steer entries."""
    if payload_hint:
        return str(payload_hint)
    texts = [str(e.get("message") or "").strip() for e in entries if e.get("message")]
    intervention: Optional[dict[str, Any]] = None
    priority = 0
    preempt = False
    for entry in reversed(entries):
        if entry.get("intervention") and intervention is None:
            intervention = entry.get("intervention")
        priority = max(priority, int(entry.get("priority") or 0))
        preempt = preempt or bool(entry.get("preempt"))
    combined = "\n".join(texts)
    tier = classify_steer_interrupt(
        combined,
        intervention=intervention if isinstance(intervention, dict) else None,
        priority=priority,
        preempt=preempt,
    )
    return steer_action_hint(
        tier,
        combined,
        intervention if isinstance(intervention, dict) else None,
    )


def init_foreground_epoch_on_run(state: AgentState, *, run_id: str) -> AgentState:
    """Ensure interrupt_context has epoch and execution_run records the bound run."""
    ctx = ensure_interrupt_context(state)
    if "foreground_epoch" not in ctx:
        ctx["foreground_epoch"] = 0
    run_meta = dict(state.get("execution_run") or {})
    run_meta["run_id"] = str(run_id)
    run_meta["foreground_epoch"] = int(ctx.get("foreground_epoch") or 0)
    run_meta["started_at"] = _now_iso()
    return merge_state(state, interrupt_context=ctx, execution_run=run_meta)
