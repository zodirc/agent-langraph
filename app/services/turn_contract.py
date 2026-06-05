"""
Turn contract — single executable plan for one planning → execution cycle.

Planning LLM may emit ``turn_contract`` or ``mission_intervention``; runtime materializes
``writing_intent``, ``selected_tools``, and routing hints from one source to avoid
step_policy overriding steer intent.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.manuscript_service import WRITING_TOOL_NAMES, split_execution_tools
from app.services.mission_intervention import (
    apply_intervention_to_payload,
    intervention_from_payload,
    intervention_to_writing_intent,
    is_forced,
)

_WRITE_ACTIONS = frozenset(
    {"append_body", "write_body", "write_outline", "reset_body", "rewrite_outline"}
)


def contract_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    block = payload.get("turn_contract")
    if block is None:
        return None
    if isinstance(block, dict) and block.get("primary_op"):
        return dict(block)
    return None


def contract_blocks_writing(payload: dict[str, Any]) -> bool:
    contract = contract_from_payload(payload)
    if not contract:
        return False
    forbid = {str(x) for x in (contract.get("forbid") or [])}
    if forbid & _WRITE_ACTIONS:
        return True
    primary = str(contract.get("primary_op") or "")
    if primary in ("edit_plot", "review_outline", "run_tools", "batch_unit_quality"):
        return True
    intent = payload.get("writing_intent") or {}
    if intent.get("enabled") is False and primary in ("edit_plot", "review_outline", "run_tools"):
        return True
    return False


def contract_tool_names(payload: dict[str, Any]) -> list[str]:
    contract = contract_from_payload(payload)
    if contract is None:
        return [
            str(t)
            for t in (payload.get("selected_tools") or [])
            if str(t) not in WRITING_TOOL_NAMES
        ]

    tools = list(contract.get("tools") or [])
    if tools:
        return [str(t) for t in tools if str(t) not in WRITING_TOOL_NAMES]
    out: list[str] = []
    for op in contract.get("ops") or []:
        if not isinstance(op, dict):
            continue
        tool = str(op.get("tool") or "").strip()
        if tool and tool not in WRITING_TOOL_NAMES and tool not in out:
            out.append(tool)
    return out


def augment_intervention_from_planning(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    steer_planning_turn: bool,
) -> dict[str, Any]:
    """Infer mission_intervention from structured planning when the model omitted it."""
    existing = intervention_from_payload(payload)
    if existing:
        if steer_planning_turn and str(existing.get("action")) == "edit_plot" and not is_forced(existing):
            return apply_intervention_to_payload(
                payload,
                {**existing, "force": True},
            )
        return payload

    raw_contract = result.get("turn_contract")
    if isinstance(raw_contract, dict) and raw_contract.get("primary_op"):
        return payload

    from app.services.writing.intent_parser import parse_intent_from_planning

    intent = parse_intent_from_planning(result, payload)
    if intent and intent.action in ("edit_plot", "review_outline", "reset_body", "rewrite_outline"):
        if steer_planning_turn and intent.action == "edit_plot" and not intent.force:
            intent.force = True
        return apply_intervention_to_payload(
            payload,
            {
                "action": intent.action,
                "force": intent.force,
                "reason": intent.reason,
                "intent_anchor": intent.anchor.to_dict(),
            },
        )
    return payload


def build_turn_contract(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    steer_planning_turn: bool,
) -> dict[str, Any]:
    """Compose turn_contract from explicit LLM block, intervention, or structured plan signals."""
    raw = result.get("turn_contract")
    if isinstance(raw, dict) and raw.get("primary_op"):
        contract = dict(raw)
    else:
        intervention = intervention_from_payload(payload)
        if intervention:
            contract = _contract_from_intervention(intervention, payload, steer_planning_turn=steer_planning_turn)
        else:
            contract = _contract_from_planning_signals(result, payload, steer_planning_turn=steer_planning_turn)

    if steer_planning_turn and str(contract.get("primary_op")) == "edit_plot":
        contract["override_step_policy"] = True
        forbid = set(str(x) for x in (contract.get("forbid") or []))
        forbid.update({"append_body", "write_body"})
        contract["forbid"] = sorted(forbid)

    return contract


def _contract_from_intervention(
    intervention: dict[str, Any],
    payload: dict[str, Any],
    *,
    steer_planning_turn: bool,
) -> dict[str, Any]:
    from app.services.writing.command_builder import build_writing_command
    from app.services.writing.intent_parser import parse_intent_from_intervention

    action = str(intervention.get("action") or "")
    intent = parse_intent_from_intervention(intervention)
    tools = [str(t) for t in (payload.get("selected_tools") or []) if str(t) not in WRITING_TOOL_NAMES]

    if action == "edit_plot":
        command = build_writing_command(
            {"input_payload": payload, "mission": payload.get("mission") or {}},
            intent=intent,
        )
        filename = command.target_filename
        has_anchor = bool(command.edit_spec.get("old_text"))
        ops: list[dict[str, Any]] = [
            {
                "op": "read",
                "tool": "read_text_artifact",
                "target": filename,
            },
            {
                "op": "edit",
                "tool": "edit_text_artifact",
                "target": filename,
                "anchor": "exact" if has_anchor else "from_read",
            },
        ]
        return {
            "intent_kind": "steer_material_change",
            "primary_op": "edit_plot",
            "ops": ops,
            "tools": tools or ["read_text_artifact", "edit_text_artifact"],
            "forbid": ["append_body", "write_body"],
            "override_step_policy": is_forced(intervention) or steer_planning_turn,
            "user_visible_reason": str(intervention.get("reason") or ""),
        }

    if action == "rewrite_outline":
        return {
            "intent_kind": "steer_material_change",
            "primary_op": "write_outline",
            "ops": [{"op": "write", "action": "write_outline"}],
            "tools": tools,
            "forbid": ["append_body"],
            "override_step_policy": is_forced(intervention),
            "user_visible_reason": str(intervention.get("reason") or ""),
        }

    if action == "reset_body":
        return {
            "intent_kind": "steer_material_change",
            "primary_op": "reset_body",
            "ops": [{"op": "write", "action": "reset_body"}],
            "tools": tools,
            "forbid": ["append_body"],
            "override_step_policy": is_forced(intervention),
            "user_visible_reason": str(intervention.get("reason") or ""),
        }

    if action == "review_outline":
        return {
            "intent_kind": "inspect",
            "primary_op": "review_outline",
            "ops": [],
            "tools": tools,
            "forbid": list(_WRITE_ACTIONS),
            "override_step_policy": True,
            "user_visible_reason": str(intervention.get("reason") or ""),
        }

    if action == "batch_unit_quality":
        return {
            "intent_kind": "batch_quality",
            "primary_op": "batch_unit_quality",
            "ops": [{"op": "evaluate", "unit": "chapter"}],
            "tools": tools or ["read_text_artifact"],
            "forbid": list(_WRITE_ACTIONS),
            "override_step_policy": True,
            "user_visible_reason": str(intervention.get("reason") or ""),
        }

    return {
        "intent_kind": "mission_control",
        "primary_op": action or "continue",
        "ops": [],
        "tools": tools,
        "forbid": [],
        "override_step_policy": is_forced(intervention),
        "user_visible_reason": str(intervention.get("reason") or ""),
    }


def _contract_from_planning_signals(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    steer_planning_turn: bool,
) -> dict[str, Any]:
    wi = result.get("writing_intent") if isinstance(result.get("writing_intent"), dict) else {}
    tools = [str(t) for t in (result.get("selected_tools") or []) if str(t) not in WRITING_TOOL_NAMES]
    action = str(wi.get("action") or "append_body")

    if not wi.get("enabled") and action == "edit_plot":
        return _contract_from_intervention(
            {
                "action": "edit_plot",
                "force": steer_planning_turn,
                "intent_anchor": {},
            },
            payload,
            steer_planning_turn=steer_planning_turn,
        )

    if wi.get("enabled"):
        return {
            "intent_kind": "forward_write",
            "primary_op": action,
            "ops": [{"op": "write", "action": action}],
            "tools": tools,
            "forbid": [],
            "override_step_policy": False,
            "user_visible_reason": "",
        }

    if action in ("pause", "batch_unit_quality"):
        forbid = list(_WRITE_ACTIONS)
        return {
            "intent_kind": "mission_control" if action == "pause" else "batch_quality",
            "primary_op": action,
            "ops": [{"op": "evaluate", "unit": "chapter"}] if action == "batch_unit_quality" else [],
            "tools": tools,
            "forbid": forbid,
            "override_step_policy": True,
            "user_visible_reason": "",
        }

    return {
        "intent_kind": "reasoning_only",
        "primary_op": "reasoning",
        "ops": [],
        "tools": tools,
        "forbid": list(_WRITE_ACTIONS) if tools else [],
        "override_step_policy": bool(tools),
        "user_visible_reason": "",
    }


def materialize_writing_intent_from_contract(
    contract: dict[str, Any],
    state: AgentState,
    *,
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Map contract → writing_intent without applying step_policy."""
    intervention = intervention_from_payload(state.get("input_payload") or {})
    step = int(state.get("mission_step") or 1)
    primary = str(contract.get("primary_op") or "")

    if intervention and is_forced(intervention):
        intent = intervention_to_writing_intent(intervention, mission_step=step)
        intent["contract_source"] = True
        return intent

    from app.services.writing.command_builder import COMMAND_ACTIONS
    from app.services.writing.command_builder import build_writing_command
    from app.services.writing.command_intent import command_to_writing_intent

    if primary in COMMAND_ACTIONS:
        command = build_writing_command(state, action=primary)
        intent = command_to_writing_intent(command, mission_step=step)
        intent["contract_source"] = True
        intent["source"] = "turn_contract"
        return intent

    if primary == "batch_unit_quality":
        return {
            "enabled": False,
            "action": "batch_unit_quality",
            "source": "turn_contract",
            "mission_step": step,
            "contract_source": True,
        }

    if primary in ("append_body",):
        from app.domain.mission import StepPolicy

        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        return {
            "enabled": True,
            "action": primary,
            "source": "turn_contract",
            "mission_step": step,
            "contract_source": True,
            "target_chars": policy.chars_per_step,
        }

    return {
        "enabled": False,
        "action": primary or "continue",
        "source": "turn_contract",
        "mission_step": step,
        "contract_source": True,
    }


def apply_turn_contract_to_payload(
    payload: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Persist contract; tool routing for command-driven ops is deferred to executor."""
    out = {**payload, "turn_contract": contract}
    primary = str(contract.get("primary_op") or "")
    from app.services.writing.command_builder import COMMAND_ACTIONS

    if primary in COMMAND_ACTIONS and out.get("writing_command"):
        return out

    tools = contract_tool_names(out)
    if tools:
        out["selected_tools"] = tools
        out["tool_stages"] = payload.get("tool_stages")
    else:
        out["selected_tools"] = None
        out["tool_stages"] = None
    if contract.get("override_step_policy"):
        stages = payload.get("tool_stages")
        if not stages and "read_text_artifact" in tools and "edit_text_artifact" in tools:
            out["tool_stages"] = [["read_text_artifact"], ["edit_text_artifact"]]
    return out


def finalize_turn_execution_plan(
    result: dict[str, Any],
    payload: dict[str, Any],
    state: AgentState,
    exec_tools: list[str],
    *,
    mission: dict[str, Any],
    steer_planning_turn: bool,
) -> tuple[dict[str, Any], list[str]]:
    """
  After planning LLM + intervention merge: build contract, materialize payload, return exec tools.
    """
    payload = augment_intervention_from_planning(
        result, payload, steer_planning_turn=steer_planning_turn
    )
    contract = build_turn_contract(result, payload, steer_planning_turn=steer_planning_turn)
    from app.services.turn_contract_lifecycle import sanitize_turn_contract

    contract = sanitize_turn_contract(contract, payload, state)
    payload = apply_turn_contract_to_payload(payload, contract)

    if str(mission.get("kind") or "").lower() == "writing":
        primary = str(contract.get("primary_op") or "")
        use_contract_intent = bool(
            contract.get("override_step_policy")
            or contract_blocks_writing(payload)
            or primary in ("batch_unit_quality", "pause", "edit_plot", "review_outline")
        )
        from app.services.writing.command_builder import COMMAND_ACTIONS

        if use_contract_intent:
            if primary in COMMAND_ACTIONS and (payload.get("writing_command") or payload.get("writing_intent_record")):
                payload.pop("writing_intent", None)
            else:
                payload["writing_intent"] = materialize_writing_intent_from_contract(
                    contract, state, mission=mission
                )
        else:
            from app.services.mission_schema import resolve_writing_intent_for_step

            payload["writing_intent"] = resolve_writing_intent_for_step(state, mission=mission)

    merged_tools = contract_tool_names(payload)
    if merged_tools:
        exec_tools = merged_tools
    else:
        non_writing, _ = split_execution_tools(exec_tools)
        exec_tools = non_writing

    from app.services.turn_contract_lifecycle import clear_contract_replan_requirement

    payload = clear_contract_replan_requirement(payload)

    return payload, exec_tools


def validate_turn_contract_execution(state: AgentState) -> list[str]:
    """Post-hoc issues for reflection / replan (contract vs turn_facts)."""
    payload = state.get("input_payload") or {}
    contract = contract_from_payload(payload)
    if not contract:
        return []

    issues: list[str] = []
    from app.services.fact_layer import build_turn_facts

    facts = build_turn_facts(state) or {}
    executed: set[str] = set()
    for line in facts.get("tools_executed") or []:
        if isinstance(line, dict) and line.get("tool"):
            executed.add(str(line["tool"]))
        elif isinstance(line, str):
            executed.add(line)
    expected_tools = set(contract_tool_names(payload))

    for tool in expected_tools:
        if tool not in executed:
            issues.append(f"contract_expected_tool_missing:{tool}")

    if contract_blocks_writing(payload):
        for action in facts.get("executed_actions") or []:
            act = str(action).lower()
            if "append" in act or "write_body" in act:
                issues.append("contract_forbid_writing_violated")
                break

    primary = str(contract.get("primary_op") or "")
    if primary == "batch_unit_quality":
        actions = {str(a) for a in (facts.get("executed_actions") or [])}
        obs = state.get("observation") or {}
        actions.update(str(a) for a in (obs.get("executed_actions") or []))
        if not any("review_chapter" in a or a == "writing:review_chapter" for a in actions):
            for entry in state.get("audit_log") or []:
                detail = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
                phase = str(detail.get("writing_phase") or detail.get("action") or "")
                if phase == "review_chapter" and entry.get("action") == "success":
                    break
            else:
                issues.append("contract_batch_unit_no_review_executed")

    return issues


_EXECUTION_PRIMARY_OPS = frozenset(
    {
        "batch_unit_quality",
        "edit_plot",
        "review_outline",
        "run_tools",
        "write_outline",
        "reset_body",
        "append_body",
        "write_body",
    }
)


def contract_requires_side_effects(
    payload: dict[str, Any],
    *,
    state: AgentState | dict[str, Any] | None = None,
) -> bool:
    """True when this turn must mutate artifacts or run tools — not reasoning-only."""
    contract = contract_from_payload(payload)
    if not contract:
        return False
    primary = str(contract.get("primary_op") or "")
    if primary in ("reasoning", "explain_only"):
        return False
    if primary == "pause" and state is not None:
        from app.services.turn_kind import agenda_has_executor_pending

        if agenda_has_executor_pending(state):  # type: ignore[arg-type]
            return True
        return False
    if contract_tool_names(payload):
        return True
    ops = contract.get("ops") or []
    if ops:
        return True
    if primary in _EXECUTION_PRIMARY_OPS:
        return True
    intent = payload.get("writing_intent") or {}
    return bool(intent.get("enabled"))


def is_turn_contract_fulfilled(state: AgentState) -> bool:
    """Mechanical check: contract side effects were observed in turn_facts."""
    payload = state.get("input_payload") or {}
    if not contract_requires_side_effects(payload, state=state):
        return True
    issues = validate_turn_contract_execution(state)
    if issues:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_contract_event("unfulfilled")
    return len(issues) == 0


def record_contract_fulfilled(state: AgentState) -> None:
    """Metrics hook when a side-effect contract completes."""
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_contract_event("fulfilled")


def outline_artifact_status(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve outline artifact on disk/state (existence, not merely byte threshold)."""
    task_id = str(state.get("task_id") or "")
    stored = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else {}
    from app.services.manuscript_service import resolve_manuscript

    ms = resolve_manuscript(task_id, stored or None) if task_id else None
    if ms is None:
        from app.services.manuscript_service import Manuscript

        ms = Manuscript(task_id=task_id or "unknown")
    path = str(ms.outline_path or stored.get("outline_path") or "").strip()
    nbytes = max(int(ms.outline_bytes or 0), int(stored.get("outline_bytes") or 0))
    on_disk = bool(ms.outline_path)
    exists = bool(path) and (on_disk or nbytes > 0)
    return {
        "outline_path": path or None,
        "outline_bytes": nbytes,
        "outline_exists": exists,
    }


def steer_replan_outline_plan(state: dict[str, Any], *, steer: str) -> dict[str, Any]:
    """
    Steer replan outline routing (step 3):
    outline file exists → edit_plot; otherwise → full write_outline with steer constraints.
    """
    status = outline_artifact_status(state)
    steer = str(steer or "").strip()
    if status["outline_exists"]:
        return {
            "plan": [
                "read outline for anchors",
                "edit plot per user steer",
                "align written body if needed",
            ],
            "writing_intent": {"enabled": False, "action": "edit_plot"},
            "mission_intervention": {
                "action": "edit_plot",
                "force": True,
                "reason": steer[:240],
                "intent_anchor": {
                    "steer_correction": steer[-2000:],
                    "target_hint": "outline",
                },
            },
            "work_plan_patch": {
                "cancel_kinds": ["write_outline", "append_body"],
                "prepend": [{"kind": "edit_plot", "title": "edit plot per steer"}],
            },
            "skip_retrieval": True,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "steer_replan_edit_plot",
            "steer_outline_route": "modify",
        }

    return {
        "plan": [
            "write outline per steer constraints",
            "append body via mission loop",
        ],
        "writing_intent": {
            "enabled": True,
            "action": "write_outline",
            "reason": steer[:240],
        },
        "mission_intervention": {
            "action": "rewrite_outline",
            "force": True,
            "reason": steer[:240],
        },
        "work_plan_patch": {
            "cancel_kinds": ["append_body", "edit_plot"],
            "prepend": [{"kind": "write_outline", "title": "write outline per steer"}],
        },
        "skip_retrieval": False,
        "risk_level": "LOW",
        "parser_fallback": True,
        "fallback_reason": "steer_replan_write_outline",
        "steer_outline_route": "rewrite",
    }


def apply_steer_replan_outline_route(
    state: dict[str, Any],
    planning_result: dict[str, Any],
) -> dict[str, Any]:
    """After planning LLM: enforce exists→modify / missing→rewrite."""
    payload = dict(state.get("input_payload") or {})
    from app.services.mission_steer import planning_steer_replan_active

    if not planning_steer_replan_active(payload, state):
        return planning_result

    steer = str(payload.get("latest_steer_message") or payload.get("goal") or "").strip()
    if not steer:
        return planning_result

    mission = state.get("mission") or payload.get("mission")
    if not isinstance(mission, dict) or str(mission.get("kind") or "").lower() != "writing":
        return planning_result

    routed = steer_replan_outline_plan(state, steer=steer)
    out = dict(planning_result)
    for key in (
        "plan",
        "writing_intent",
        "mission_intervention",
        "work_plan_patch",
        "skip_retrieval",
        "fallback_reason",
        "steer_outline_route",
        "parser_fallback",
    ):
        if key in routed:
            out[key] = routed[key]
    out["risk_level"] = str(out.get("risk_level") or routed.get("risk_level") or "LOW")
    return out


def steer_replan_planning_fallback_from_state(
    state: dict[str, Any] | None,
) -> Optional[dict[str, Any]]:
    """
    Steer replan on writing missions: route outline by artifact existence.

    Intent comes from latest_steer_message; action is deterministic (modify vs rewrite).
    """
    if not isinstance(state, dict):
        return None

    payload = dict(state.get("input_payload") or {})
    from app.services.mission_steer import planning_steer_replan_active

    if not planning_steer_replan_active(payload, state):
        return None

    steer = str(payload.get("latest_steer_message") or "").strip()
    if not steer:
        return None

    mission = state.get("mission") or payload.get("mission")
    if not isinstance(mission, dict) or str(mission.get("kind") or "").lower() != "writing":
        return None

    return steer_replan_outline_plan(state, steer=steer)


def planning_fallback_from_state(state: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    """
    Deterministic planning payload when the model returns empty or non-JSON output.

    Uses mission/work_plan structure only (no goal keyword matching).
    """
    if not isinstance(state, dict):
        return None

    payload = dict(state.get("input_payload") or {})
    mission = state.get("mission") or payload.get("mission")
    if not isinstance(mission, dict) or str(mission.get("kind") or "").lower() != "writing":
        return None

    goal = str(payload.get("goal") or "").strip()
    if not goal:
        return None

    ms = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else {}
    outline_path = str(
        ms.get("outline_path")
        or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
    )
    outline_bytes = int(ms.get("outline_bytes") or 0)

    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    work_plan = progress.get("work_plan") if isinstance(progress.get("work_plan"), dict) else {}
    items = list(work_plan.get("items") or [])

    def _pending_kind(kind: str) -> bool:
        return any(
            str(row.get("kind") or "") == kind
            and str(row.get("status") or "pending") in ("pending", "active")
            for row in items
            if isinstance(row, dict)
        )

    intervention = intervention_from_payload(payload)

    from app.services.mission.batch_unit_work_plan import build_batch_unit_planning_fallback

    batch_fb = build_batch_unit_planning_fallback(state)  # type: ignore[arg-type]
    if batch_fb:
        return batch_fb

    def _failed_edit_plot() -> bool:
        return any(
            str(row.get("kind") or "") == "edit_plot"
            and str(row.get("status") or "") == "failed"
            for row in items
            if isinstance(row, dict)
        )

    edit_plot_pending = _pending_kind("edit_plot") or (
        intervention and str(intervention.get("action")) == "edit_plot"
    )
    retry_blocked = bool((payload.get("command_retry_blocked") or {}).get("blocked"))
    if edit_plot_pending and (_failed_edit_plot() or retry_blocked):
        edit_plot_pending = False

    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        return None

    if edit_plot_pending or (
        outline_bytes > 0
        and (payload.get("require_planning_after_steer") or payload.get("steer_applied_at"))
        and not payload.get("execution_grant")
    ):
        return {
            "plan": [
                "read outline for anchors",
                "edit plot per user steer",
                "align written body if needed",
            ],
            "writing_intent": {"enabled": False, "action": "edit_plot"},
            "mission_intervention": {
                "action": "edit_plot",
                "force": True,
                "reason": goal[:240],
                "intent_anchor": {"target_hint": "outline"},
            },
            "skip_retrieval": True,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "steer_material_change",
        }

    if _pending_kind("write_outline") or (
        intervention and str(intervention.get("action")) == "rewrite_outline"
    ):
        return {
            "plan": ["rewrite outline via writing"],
            "selected_tools": [],
            "writing_intent": {
                "enabled": True,
                "action": "write_outline",
            },
            "skip_retrieval": False,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "rewrite_outline_queue",
        }

    from app.services.turn_contract_lifecycle import contract_replan_required

    def _outline_work_pending() -> bool:
        for row in items:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "pending")
            if status not in ("pending", "active", "running"):
                continue
            kind = str(row.get("kind") or "")
            title = str(row.get("title") or "").lower()
            if kind == "write_outline":
                return True
            if kind == "plan_step" and "outline" in title:
                return True
        return False

    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
    first_step = str(policy.get("first_step") or "outline")
    needs_outline = outline_bytes <= 0 and first_step in ("outline", "write_outline")

    from app.services.intent_composer import grant_may_mechanical_forward

    if payload.get("execution_grant") and grant_may_mechanical_forward(payload, state=state):  # type: ignore[arg-type]
        policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
        action = str(policy.get("then") or "append_body")
        return {
            "plan": ["append next chapter via mission"],
            "selected_tools": [],
            "writing_intent": {"enabled": True, "action": action},
            "skip_retrieval": False,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "execution_grant_forward",
        }

    if contract_replan_required(payload) and (needs_outline or _outline_work_pending()):
        return {
            "plan": ["write outline via writing"],
            "selected_tools": [],
            "writing_intent": {
                "enabled": True,
                "action": "write_outline",
            },
            "skip_retrieval": False,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "contract_replan_write_outline",
        }

    return None
