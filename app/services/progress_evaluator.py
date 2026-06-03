"""Mission 进度评估
evaluate_mission_control(state) — mission_eval + mission_decide 使用
  检查 Checks: success_criteria, budget max_steps, failures, steer pause,
              stepwise_pause, orchestration work_plan 完成度
  返回 EvalResult: done, action (continue|finish|pause|escalate), reason
Domain pack 可覆盖 evaluate_success

Progress evaluation — machine-checkable termination.
suggest_pause（writing 等）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.domain.mission import SuccessCriteria
from app.domain.packs.registry import get_domain_pack
from app.runtime.state import AgentState


@dataclass
class EvalResult:
    done: bool
    reason: str
    action: str  # continue | finish | pause | escalate
    pause_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "done": self.done,
            "reason": self.reason,
            "action": self.action,
        }
        if self.pause_reason:
            out["pause_reason"] = self.pause_reason
        return out


def evaluate_success_criteria(
    mission: dict[str, Any],
    progress: dict[str, Any],
    observation: dict[str, Any],
) -> tuple[bool, str]:
    """Return (success, reason). Delegates to domain pack when registered."""
    kind = str(mission.get("kind", "single_turn"))
    try:
        pack = get_domain_pack(kind)
        if hasattr(pack, "evaluate_success"):
            return pack.evaluate_success(mission, progress, observation)
    except KeyError:
        pass

    criteria = mission.get("success_criteria") or {}
    sc = SuccessCriteria.from_dict(criteria if isinstance(criteria, dict) else {})
    metrics = progress.get("metrics") or {}

    if sc.type == "metric_gte" and sc.metric:
        current = float(metrics.get(sc.metric, 0))
        target = float(sc.target)
        if current >= target:
            return True, f"{sc.metric} {current} >= {target}"
        return False, f"{sc.metric} {current} < {target}"

    if sc.type == "steps_done":
        steps = int(progress.get("steps_completed", 0))
        target = int(sc.target or 1)
        if steps >= target:
            return True, f"steps {steps} >= {target}"
        return False, f"steps {steps} < {target}"

    if sc.type == "no_failures" and not observation.get("has_failures"):
        if progress.get("steps_completed", 0) >= 1:
            return True, "step completed without failures"
    return False, "criteria not met"


def _parse_iso_timestamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed_wall_seconds(started_at: str) -> float | None:
    started = _parse_iso_timestamp(started_at)
    if started is None:
        return None
    return (datetime.now(timezone.utc) - started).total_seconds()


def evaluate_mission_control(state: AgentState) -> EvalResult:
    """
    Decide whether the mission loop should finish, pause, or continue.
    Program predicates take precedence over LLM step_decision.
    """
    from app.services.mission_intervention import intervention_from_payload, is_forced
    from app.services.mission_orchestrator import (
        get_current_work_item,
        orchestration_enabled,
        stepwise_pause,
        work_plan_completed,
    )
    from app.services.state_store import get_state_store

    from app.runtime.state import TaskStatus

    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    observation = state.get("observation") or {}
    step = int(state.get("mission_step") or 0)

    status = str(state.get("status") or "")
    if status in (TaskStatus.FAILED.value, TaskStatus.DEAD_LETTER.value):
        detail = (state.get("errors") or ["unknown"])[-1]
        return EvalResult(
            done=True,
            reason=f"mission halted ({status}): {detail}",
            action="pause",
        )

    payload_early = state.get("input_payload") or {}
    stored = get_state_store().load(state["task_id"]) or state
    payload_merged = {**(stored.get("input_payload") or {}), **payload_early}
    intervention_early = intervention_from_payload(payload_merged)
    if intervention_early and is_forced(intervention_early):
        material = frozenset(
            {"rewrite_outline", "reset_body", "edit_plot", "run_tools", "enqueue_work"}
        )
        action = str(intervention_early.get("action") or "")
        if action == "pause":
            from app.services.mission_execution import PAUSE_FORCED

            return EvalResult(
                done=True,
                reason=intervention_early.get("reason") or "forced intervention pause",
                action="pause",
                pause_reason=PAUSE_FORCED,
            )
        if action in material:
            return EvalResult(
                done=False,
                reason=f"forced intervention {action}",
                action="continue",
            )
        if action == "continue":
            return EvalResult(
                done=False,
                reason="forced intervention continue",
                action="continue",
            )

    from app.services.state_store import merge_input_payload_for_gates
    from app.services.mission_execution import (
        PAUSE_FORCED,
        PAUSE_GATE_INTENT,
        PAUSE_GATE_OUTCOME,
        PAUSE_HUMAN_GATE,
        PAUSE_STEP_CHECKPOINT,
        PAUSE_STEER_QUEUED,
        has_execution_grant,
    )
    from app.services.mission_steer import pending_has_forced_action, pending_steer_is_set

    payload_for_grant = merge_input_payload_for_gates(state, stored)
    pending = stored.get("pending_user_message") or state.get("pending_user_message")
    if pending_has_forced_action(pending, "pause"):
        return EvalResult(
            done=True,
            reason="forced pause requested",
            action="pause",
            pause_reason=PAUSE_FORCED,
        )
    if has_execution_grant(payload_for_grant):
        return EvalResult(
            done=False,
            reason="execution grant: run next orchestrated step",
            action="continue",
        )
    else:
        if pending_steer_is_set(pending):
            return EvalResult(
                done=True,
                reason="user steer message queued",
                action="pause",
                pause_reason=PAUSE_STEER_QUEUED,
            )

    payload_for_confirm = merge_input_payload_for_gates(state, stored)
    from app.services.mission_steer_confirm import steer_confirmation_pending

    if steer_confirmation_pending(payload_for_confirm):
        return EvalResult(
            done=True,
            reason="steer intent confirmation required before execute",
            action="pause",
            pause_reason=PAUSE_GATE_INTENT,
        )

    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending

    if steer_outcome_confirmation_pending(payload_for_confirm):
        return EvalResult(
            done=True,
            reason="steer outcome confirmation required after work item",
            action="pause",
            pause_reason=PAUSE_GATE_OUTCOME,
        )

    if orchestration_enabled(mission):
        item = get_current_work_item(state)
        if item and str(item.get("kind")) == "human_gate" and not observation.get(
            "has_failures"
        ):
            return EvalResult(
                done=True,
                reason=str(item.get("title") or "human checkpoint"),
                action="pause",
                pause_reason=PAUSE_HUMAN_GATE,
            )
        if work_plan_completed(state):
            return EvalResult(
                done=True,
                reason="orchestrated work plan completed",
                action="finish",
            )
        if stepwise_pause(mission) and not observation.get("has_failures"):
            if int(state.get("mission_step") or 0) >= 1:
                return EvalResult(
                    done=True,
                    reason="stepwise orchestration: awaiting user steer or resume",
                    action="pause",
                    pause_reason=PAUSE_STEP_CHECKPOINT,
                )

    payload = state.get("input_payload") or stored.get("input_payload") or {}
    intervention = intervention_from_payload(payload)
    if intervention and is_forced(intervention):
        if intervention.get("action") == "pause":
            return EvalResult(
                done=True,
                reason=intervention.get("reason") or "forced intervention pause",
                action="pause",
            )
        if intervention.get("action") == "continue":
            return EvalResult(
                done=False,
                reason="forced intervention continue",
                action="continue",
            )

    budget_raw = mission.get("budget") or {}
    cap = int(getattr(settings, "MISSION_STEPS_HARD_CAP", 500))
    max_steps = int(budget_raw.get("max_steps") or getattr(settings, "MISSION_MAX_STEPS", cap))
    max_steps = min(max(1, max_steps), cap)
    max_wall = int(
        budget_raw.get("max_wall_sec")
        or getattr(settings, "MISSION_MAX_WALL_SEC", 3600)
    )
    max_failures = int(
        budget_raw.get("max_failures")
        or getattr(settings, "MISSION_MAX_FAILURES", 3)
    )

    started = progress.get("started_at")
    if started and max_wall > 0:
        elapsed = _elapsed_wall_seconds(str(started))
        if elapsed is not None and elapsed >= max_wall:
            return EvalResult(
                done=True,
                reason=f"wall_clock {int(elapsed)}s >= max_wall_sec {max_wall}",
                action="pause",
            )

    failures = int(progress.get("consecutive_failures", 0))
    if failures >= max_failures:
        return EvalResult(
            done=True,
            reason=f"consecutive_failures {failures} >= {max_failures}",
            action="pause",
        )

    if observation.get("has_failures") and failures < max_failures:
        from app.services.turn_contract_lifecycle import (
            contract_replan_required,
            detect_non_recoverable_step_failure,
        )

        payload_for_replan = state.get("input_payload") or stored.get("input_payload") or {}
        if contract_replan_required(payload_for_replan):
            return EvalResult(
                done=False,
                reason="non_recoverable failure: replan required before next step",
                action="continue",
            )
        if detect_non_recoverable_step_failure(state):
            return EvalResult(
                done=False,
                reason="non_recoverable failure pending replan",
                action="continue",
            )
        return EvalResult(
            done=False,
            reason="last step had failures",
            action="continue",
        )

    success, reason = evaluate_success_criteria(mission, progress, observation)
    if success:
        return EvalResult(done=True, reason=reason, action="finish")

    if step >= max_steps:
        return EvalResult(
            done=True,
            reason=f"mission_step {step} >= max_steps {max_steps}",
            action="pause",
        )

    constraints = mission.get("constraints") or {}
    if constraints.get("no_human") and str(mission.get("execution_mode")) == "autonomous":
        return EvalResult(done=False, reason=reason or "continue autonomous", action="continue")

    return EvalResult(done=False, reason=reason or "in progress", action="continue")
