"""单一收敛门 converge —— 统一 agent 循环的唯一收敛判定（unified-core WP-2）。

把历史上分散在 ``progress_evaluator`` / ``revision_done`` / ``turn_guard`` /
``revision_loop_guard`` 的 mission/revision 收敛逻辑收拢为一个纯函数：

    evaluate_convergence(state) -> ConvergeResult(done, next, reason)

输入只读 ``turn_facts`` / ``tool_results`` / ``turn_contract``(input_payload) /
``reasoning_result``，不做任何 state 写入；replan 预算等副作用由路由器负责。

判定优先级：
1. 编辑诚实：本轮尝试过 ``edit_artifact`` → 0 替换不算成功（replan），
   全部落盘才算 done；
2. read-loop：连续只读无副作用 → safe finalize（不再回 plan 空转）；
3. 契约副作用：turn 契约要求 write/edit/run 且未兑现 → replan，已兑现 → done；
4. answer：reasoning 产出非空 → done。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# Suggested next move (caller maps to concrete graph nodes):
NEXT_PROCEED = "proceed"   # keep going down the normal pipeline
NEXT_REPLAN = "replan"     # goal not met and progress is possible -> back to plan
NEXT_FINALIZE = "finalize" # stuck/looping or budget exhausted -> safe finalize

# Consecutive successful reads with zero side effects before declaring a loop.
READ_LOOP_THRESHOLD = 3

_SIDE_EFFECT_TOOLS = frozenset(
    {
        "write_text_artifact",
        "append_text_artifact",
        "edit_text_artifact",
        "write_file",
        "append_file",
        "replace_in_file",
        "move_path",
        "copy_path",
        "touch_file",
        "mkdir_path",
        "rm_path",
    }
)


@dataclass(frozen=True)
class ConvergeResult:
    done: bool
    next: str  # one of NEXT_PROCEED / NEXT_REPLAN / NEXT_FINALIZE
    reason: str


def _edit_counts(
    facts: Mapping[str, Any], tool_results: list[dict[str, Any]]
) -> tuple[int, int]:
    """(attempted, applied) edit counts; turn_facts wins, tool_results is fallback."""
    if facts.get("edits_attempted"):
        return int(facts.get("edits_attempted") or 0), int(facts.get("edits_applied") or 0)
    from app.domain.action import is_edit_applied

    attempted = 0
    applied = 0
    for item in tool_results:
        if str(item.get("tool") or "") != "edit_text_artifact":
            continue
        attempted += 1
        if is_edit_applied(item.get("result") or {}):
            applied += 1
    return attempted, applied


def _pending_planned_actions(
    state: Mapping[str, Any], tool_results: list[dict[str, Any]]
) -> list[str]:
    """Planner-declared executable actions that have not run successfully yet.

    ``planned_actions`` is the turn's execution contract in the unified model:
    a turn cannot converge while a declared write/edit/run_tool has no
    successful tool result. Node-handled types (answer/retrieve/run_code) are
    excluded — dedicated nodes execute and route them.
    """
    from app.domain.action import ACTION_TOOL_NAMES

    executed_ok = {
        str(item.get("tool") or "")
        for item in tool_results
        if str(item.get("status") or "ok") in ("ok", "cached")
    }
    pending: list[str] = []
    for raw in state.get("planned_actions") or []:
        if not isinstance(raw, dict):
            continue
        a_type = str(raw.get("type") or "")
        if a_type in ("write_artifact", "edit_artifact"):
            tool = ACTION_TOOL_NAMES[a_type]
        elif a_type == "run_tool":
            tool = str((raw.get("params") or {}).get("name") or "")
        else:
            continue
        if tool and tool not in executed_ok:
            pending.append(a_type)
    return pending


def _exploratory_actions_only(state: Mapping[str, Any]) -> bool:
    """True when the planner only emitted reads/retrieves (no goal-completing action).

    That is an explicit "I need more context" plan: the loop must return to
    planning with the gathered context instead of emitting a final answer that
    promises future work.
    """
    actions = [a for a in (state.get("planned_actions") or []) if isinstance(a, dict)]
    if not actions:
        return False
    # Only concrete goal-completing action types count; a planner-set
    # completes_turn flag on a read/retrieve is a hint we deliberately ignore
    # (reading a file never satisfies a user goal by itself).
    return not any(
        str(raw.get("type") or "")
        in ("answer", "run_code", "write_artifact", "edit_artifact", "run_tool")
        for raw in actions
    )


def _read_loop_detected(tool_results: list[dict[str, Any]]) -> bool:
    """True when the turn only keeps reading without producing any side effect."""
    reads = 0
    for item in tool_results:
        tool = str(item.get("tool") or "")
        status = str(item.get("status") or "ok")
        if tool in _SIDE_EFFECT_TOOLS and status == "ok":
            return False
        if tool == "read_text_artifact" and status in ("ok", "cached"):
            reads += 1
    return reads >= READ_LOOP_THRESHOLD


def evaluate_convergence(state: Mapping[str, Any]) -> ConvergeResult:
    """Single convergence decision for the unified loop (pure, read-only)."""
    facts = state.get("turn_facts") or {}
    if not isinstance(facts, Mapping):
        facts = {}
    tool_results = [r for r in (state.get("tool_results") or []) if isinstance(r, dict)]
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    # 1) Edit honesty: zero-replacement edits are NOT success.
    attempted, applied = _edit_counts(facts, tool_results)
    if attempted:
        if applied >= attempted:
            return ConvergeResult(True, NEXT_PROCEED, "edit_applied")
        return ConvergeResult(False, NEXT_REPLAN, "edit_not_applied")

    # 2) Read loop: repeated reads with no side effects -> stop spinning.
    if _read_loop_detected(tool_results):
        return ConvergeResult(False, NEXT_FINALIZE, "read_loop")

    # 3) Planned-actions contract (unified model): declared side-effect actions
    #    must have executed; an exploration-only plan must return to planning.
    pending = _pending_planned_actions(state, tool_results)
    if pending:
        return ConvergeResult(False, NEXT_REPLAN, f"actions_pending:{','.join(pending)}")
    if _exploratory_actions_only(state) and tool_results:
        return ConvergeResult(False, NEXT_REPLAN, "exploration_needs_replan")

    # 3b) Legacy turn-contract side effects (transitional).
    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
    )

    if contract_requires_side_effects(payload, state=state):
        if is_turn_contract_fulfilled(state):
            return ConvergeResult(True, NEXT_PROCEED, "contract_fulfilled")
        return ConvergeResult(False, NEXT_REPLAN, "contract_unfulfilled")

    # 4) Answer turns: non-empty reasoning output converges.
    reasoning = state.get("reasoning_result") or {}
    answer_text = ""
    if isinstance(reasoning, Mapping):
        answer_text = str(reasoning.get("summary") or reasoning.get("answer") or "")
    if not answer_text.strip():
        answer_text = str(state.get("final_answer") or "")
    if answer_text.strip():
        return ConvergeResult(True, NEXT_PROCEED, "answer_ready")

    # No convergence signal yet: let the pipeline keep flowing (reasoning will
    # produce the answer); this is NOT a replan trigger.
    return ConvergeResult(False, NEXT_PROCEED, "no_signal")


__all__ = [
    "ConvergeResult",
    "evaluate_convergence",
    "NEXT_PROCEED",
    "NEXT_REPLAN",
    "NEXT_FINALIZE",
    "READ_LOOP_THRESHOLD",
]
