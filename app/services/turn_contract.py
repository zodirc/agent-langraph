"""Turn contract — generic execution contract for one planning → execution cycle.

unified-core WP-4: ``primary_op`` uses the Action vocabulary
(``answer`` / ``retrieve`` / ``read_artifact`` / ``write_artifact`` /
``edit_artifact`` / ``run_tool`` / ``run_code``).

The contract is a mechanical promise: which tools must run this turn and
whether the turn requires side effects. Convergence (``converge``) and the
routers consume it via ``contract_requires_side_effects`` /
``is_turn_contract_fulfilled`` / ``validate_turn_contract_execution``.

All writing/mission/steer-specific contract logic was removed; the planner's
``planned_actions`` is the single source for execution intent.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState

# Action types whose execution mutates the world (artifacts / external tools).
_SIDE_EFFECT_OPS = frozenset({"write_artifact", "edit_artifact", "run_tool", "run_code"})
# Action types (plus legacy aliases) that never require side effects.
_NO_SIDE_EFFECT_OPS = frozenset(
    {"", "answer", "retrieve", "read_artifact", "reasoning", "explain_only"}
)
# Artifact action type → backend tool that proves its execution.
_PRIMARY_OP_TOOL = {
    "read_artifact": "read_text_artifact",
    "write_artifact": "write_text_artifact",
    "edit_artifact": "edit_text_artifact",
}


def contract_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    block = payload.get("turn_contract")
    if block is None:
        return None
    if isinstance(block, dict) and block.get("primary_op"):
        return dict(block)
    return None


def contract_tool_names(payload: dict[str, Any]) -> list[str]:
    """Tools the contract expects this turn (falls back to selected_tools)."""
    contract = contract_from_payload(payload)
    if contract is None:
        return [str(t) for t in (payload.get("selected_tools") or [])]
    tools = list(contract.get("tools") or [])
    if tools:
        return [str(t) for t in tools]
    out: list[str] = []
    for op in contract.get("ops") or []:
        if not isinstance(op, dict):
            continue
        tool = str(op.get("tool") or "").strip()
        if tool and tool not in out:
            out.append(tool)
    return out


def _normalized_actions(
    result: dict[str, Any],
    payload: dict[str, Any],
    actions: Optional[list[Any]],
) -> list[dict[str, Any]]:
    raw = actions
    if raw is None:
        raw = result.get("actions")
    if not isinstance(raw, list) or not raw:
        raw = payload.get("planned_actions") or []
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "to_dict"):
            out.append(item.to_dict())
    return out


def build_turn_contract(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    actions: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """Explicit LLM contract block wins; otherwise derive from planned actions."""
    raw = result.get("turn_contract")
    if isinstance(raw, dict) and raw.get("primary_op"):
        return dict(raw)

    acts = _normalized_actions(result, payload, actions)
    types = [str(a.get("type") or "") for a in acts]
    primary = next((t for t in types if t in _SIDE_EFFECT_OPS), None)
    if primary is None:
        primary = "answer" if ("answer" in types or not types) else types[0]

    tools: list[str] = []
    for action in acts:
        a_type = str(action.get("type") or "")
        if a_type == "run_tool":
            name = str((action.get("params") or {}).get("name") or "")
        else:
            name = _PRIMARY_OP_TOOL.get(a_type, "")
        if name and name not in tools:
            tools.append(name)

    return {
        "intent_kind": "actions",
        "primary_op": primary,
        "ops": [{"op": t} for t in types],
        "tools": tools,
        "forbid": [],
        "user_visible_reason": "",
    }


def contract_requires_side_effects(
    payload: dict[str, Any],
    *,
    state: AgentState | dict[str, Any] | None = None,
) -> bool:
    """True when this turn must mutate artifacts or run tools — not answer-only."""
    contract = contract_from_payload(payload)
    if not contract:
        return False
    primary = str(contract.get("primary_op") or "")
    if primary in _SIDE_EFFECT_OPS:
        return True
    if primary in _NO_SIDE_EFFECT_OPS:
        return False
    # Unknown/legacy primary_op: fall back to declared tools/ops.
    if contract_tool_names(payload):
        return True
    return bool(contract.get("ops"))


def validate_turn_contract_execution(state: AgentState) -> list[str]:
    """Post-hoc issues for reflection / replan (contract vs turn facts)."""
    payload = state.get("input_payload") or {}
    contract = contract_from_payload(payload)
    if not contract:
        return []

    from app.services.fact_layer import build_turn_facts

    facts = build_turn_facts(state) or {}
    executed: set[str] = set()
    for line in facts.get("tools_executed") or []:
        if isinstance(line, dict) and line.get("tool"):
            executed.add(str(line["tool"]))
        elif isinstance(line, str):
            executed.add(line)

    issues: list[str] = []
    for tool in contract_tool_names(payload):
        if tool not in executed:
            issues.append(f"contract_expected_tool_missing:{tool}")

    primary = str(contract.get("primary_op") or "")
    needed = _PRIMARY_OP_TOOL.get(primary)
    if needed and needed not in executed:
        issues.append(f"contract_primary_op_unfulfilled:{primary}")

    # Edit honesty: an edit that applied zero replacements is not fulfilled.
    turn_facts = state.get("turn_facts") or {}
    if primary == "edit_artifact" and turn_facts.get("edit_applied") is False:
        issues.append("contract_edit_not_applied")

    if primary == "write_artifact":
        from app.services.artifact_write_honesty import write_unchanged_after_read

        if turn_facts.get("write_verified") is False or write_unchanged_after_read(state):
            issues.append("contract_write_unchanged")

    return issues


def is_turn_contract_fulfilled(state: AgentState) -> bool:
    """Mechanical check: contract side effects were observed in turn facts."""
    payload = state.get("input_payload") or {}
    if not contract_requires_side_effects(payload, state=state):
        return True
    issues = validate_turn_contract_execution(state)
    if issues:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_contract_event("unfulfilled")
        return False
    turn_facts = state.get("turn_facts") or {}
    if turn_facts.get("write_verified") is True:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_contract_event("artifact_edit_write_verified")
    return True


def record_contract_fulfilled(state: AgentState) -> None:
    """Metrics hook when a side-effect contract completes."""
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_contract_event("fulfilled")
