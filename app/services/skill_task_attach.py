"""图执行前将 skill 策略挂到 AgentState（graph_runner 调用 apply_skill_from_payload）。

剥离 _skill_*，运行 pre_task hook，写入 skill_runtime_policy。

Attach resolved skill policy to AgentState at task start.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, append_audit, merge_state


def skill_tool_allowlist_from_state(state: AgentState | dict[str, Any]) -> Optional[list[str]]:
    policy = state.get("skill_runtime_policy") or {}
    if not isinstance(policy, dict):
        return None
    allow = list(policy.get("resolved_tool_allowlist") or [])
    block = set(policy.get("resolved_tool_blocklist") or [])
    if not allow:
        return None
    filtered = [t for t in allow if t not in block]
    return filtered or None


def merge_domain_pack_tools_with_skill(
    pack_tools: Optional[list[str]],
    skill_tools: Optional[list[str]],
) -> Optional[list[str]]:
    if not skill_tools:
        return pack_tools
    if not pack_tools:
        return skill_tools
    merged = [t for t in pack_tools if t in skill_tools]
    return merged if merged else skill_tools


def apply_skill_from_payload(
    state: AgentState,
    payload: dict[str, Any],
) -> AgentState:
    """从 input_payload 提升 skill 字段到 state，并剥离内部键。

    Promote skill fields from input_payload onto state; strip internal keys.
    """
    policy_raw = payload.pop("_skill_policy", None)
    snapshot = payload.pop("_skill_snapshot", None)
    skill_id = payload.get("skill_id")
    if not skill_id and not policy_raw:
        return merge_state(state, input_payload=payload)

    updates: dict[str, Any] = {"input_payload": payload}
    if skill_id:
        updates["skill_id"] = skill_id
    if isinstance(snapshot, dict):
        updates["skill_snapshot"] = snapshot
        updates["skill_version"] = snapshot.get("version")
        updates["skill_source_type"] = snapshot.get("source_type")
    if isinstance(policy_raw, dict):
        from app.services.skill_hooks import HOOK_TYPE_PRE_TASK, apply_hook_stage

        policy_raw = apply_hook_stage(
            policy_raw,
            HOOK_TYPE_PRE_TASK,
            context={"skill_id": skill_id, "skill_params": payload.get("skill_params")},
        )
        updates["skill_runtime_policy"] = policy_raw
        pre = str(policy_raw.get("pre_task_overlay") or "").strip()
        if pre:
            payload = dict(payload)
            payload["skill_pre_task_note"] = pre
            updates["input_payload"] = payload
        if not updates.get("skill_version"):
            updates["skill_version"] = policy_raw.get("version")

    merged = merge_state(state, **updates)
    merged = append_audit(
        merged,
        {
            "event": "skill_attached",
            "skill_id": skill_id,
            "skill_version": updates.get("skill_version"),
            "source_type": updates.get("skill_source_type"),
        },
    )
    from app.services.skill_metrics import record_skill_task_started

    record_skill_task_started(merged)
    return merged
