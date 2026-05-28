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
    if primary in ("edit_plot", "review_outline", "run_tools"):
        return True
    intent = payload.get("writing_intent") or {}
    if intent.get("enabled") is False and primary in ("edit_plot", "review_outline", "run_tools"):
        return True
    return False


def contract_tool_names(payload: dict[str, Any]) -> list[str]:
    contract = contract_from_payload(payload) or {}
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
    payload_tools = list(payload.get("selected_tools") or [])
    for t in payload_tools:
        if str(t) not in WRITING_TOOL_NAMES and str(t) not in out:
            out.append(str(t))
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

    tools = {str(t).strip() for t in (result.get("selected_tools") or []) if str(t).strip()}
    wi = result.get("writing_intent") if isinstance(result.get("writing_intent"), dict) else {}
    action = str(wi.get("action") or result.get("writing_action") or "").strip()

    if (
        "read_text_artifact" in tools
        and "edit_text_artifact" in tools
        and (not wi.get("enabled") or action == "edit_plot")
    ):
        outline_name = str(
            getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
        )
        force = steer_planning_turn or bool(payload.get("require_planning_after_steer"))
        return apply_intervention_to_payload(
            payload,
            {
                "action": "edit_plot",
                "force": force,
                "reason": str(result.get("steer_intent_summary") or ""),
                "edit_spec": {"filename": outline_name},
                "tools": ["read_text_artifact", "edit_text_artifact"],
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
    action = str(intervention.get("action") or "")
    spec = dict(intervention.get("edit_spec") or payload.get("edit_plot_spec") or {})
    tools = list(intervention.get("tools") or payload.get("selected_tools") or [])
    tools = [str(t) for t in tools if str(t) not in WRITING_TOOL_NAMES]

    if action == "edit_plot":
        filename = str(
            spec.get("filename")
            or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
        )
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
                "anchor": "from_read" if not spec.get("old_text") else "exact",
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

    if (
        "read_text_artifact" in tools
        and "edit_text_artifact" in tools
        and (not wi.get("enabled") or action == "edit_plot")
    ):
        return _contract_from_intervention(
            {
                "action": "edit_plot",
                "force": steer_planning_turn,
                "edit_spec": {},
                "tools": tools,
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

    if primary == "edit_plot":
        spec = dict((state.get("input_payload") or {}).get("edit_plot_spec") or {})
        return {
            "enabled": False,
            "action": "edit_plot",
            "source": "turn_contract",
            "mission_step": step,
            "edit_spec": spec,
            "contract_source": True,
        }

    if primary == "review_outline":
        return {
            "enabled": False,
            "action": "review_outline",
            "source": "turn_contract",
            "mission_step": step,
            "contract_source": True,
        }

    if primary in ("write_outline", "reset_body", "append_body", "write_body"):
        from app.domain.mission import StepPolicy

        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        enabled = primary != "review_outline"
        intent: dict[str, Any] = {
            "enabled": enabled,
            "action": primary,
            "source": "turn_contract",
            "mission_step": step,
            "contract_source": True,
        }
        if primary == "write_outline":
            intent["target_chars"] = policy.outline_max_chars
        elif primary in ("append_body", "write_body", "reset_body"):
            intent["target_chars"] = policy.chars_per_step
        return intent

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
    """Persist contract and align tool routing fields on payload."""
    out = {**payload, "turn_contract": contract}
    tools = contract_tool_names(out)
    if tools:
        out["selected_tools"] = tools
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
    payload = apply_turn_contract_to_payload(payload, contract)

    if str(mission.get("kind") or "").lower() == "writing":
        if contract.get("override_step_policy"):
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
            if "append" in act or "write_body" in act or "writing" in act:
                issues.append("contract_forbid_writing_violated")
                break

    return issues


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

    edit_plot_pending = _pending_kind("edit_plot") or (
        intervention and str(intervention.get("action")) == "edit_plot"
    )
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
            "selected_tools": ["read_text_artifact", "edit_text_artifact"],
            "writing_intent": {"enabled": False, "action": "edit_plot"},
            "mission_intervention": {
                "action": "edit_plot",
                "force": True,
                "edit_spec": {"filename": outline_path},
                "tools": ["read_text_artifact", "edit_text_artifact"],
                "reason": goal[:240],
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
            "skip_retrieval": True,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "rewrite_outline_queue",
        }

    if payload.get("execution_grant"):
        policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
        action = str(policy.get("then") or "append_body")
        return {
            "plan": ["append next chapter via mission"],
            "selected_tools": [],
            "writing_intent": {"enabled": True, "action": action},
            "skip_retrieval": True,
            "risk_level": "LOW",
            "parser_fallback": True,
            "fallback_reason": "execution_grant_forward",
        }

    return None
