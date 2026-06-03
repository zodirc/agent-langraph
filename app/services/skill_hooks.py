"""受信任 Skill 插件钩子
阶段 Stages: pre_task → planning_overlay → tool_filter → candidate_score
  → output_validate → presentation_hint (apply_hook_stage).

Trusted skill hooks — declarative patches only (§14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# §14.3 hook types
HOOK_TYPE_PRE_TASK = "pre_task_hook"
HOOK_TYPE_PLANNING = "planning_overlay_hook"
HOOK_TYPE_TOOL_FILTER = "tool_filter_hook"
HOOK_TYPE_CANDIDATE_SCORE = "candidate_score_hook"
HOOK_TYPE_OUTPUT_VALIDATE = "output_validate_hook"
HOOK_TYPE_PRESENTATION = "presentation_hint_hook"

ALL_HOOK_TYPES = (
    HOOK_TYPE_PRE_TASK,
    HOOK_TYPE_PLANNING,
    HOOK_TYPE_TOOL_FILTER,
    HOOK_TYPE_CANDIDATE_SCORE,
    HOOK_TYPE_OUTPUT_VALIDATE,
    HOOK_TYPE_PRESENTATION,
)


@dataclass
class TrustedHook:
    plugin_ref: str
    factory: Callable[[dict[str, Any]], dict[str, Any]]
    hook_types: list[str] = field(default_factory=lambda: [HOOK_TYPE_PLANNING])

    def patch(self, context: dict[str, Any]) -> dict[str, Any]:
        raw = self.factory(context)
        return raw if isinstance(raw, dict) else {}


_TRUSTED_HOOKS: dict[str, TrustedHook] = {}


def register_trusted_hook(
    plugin_ref: str,
    factory: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    hook_types: Optional[list[str]] = None,
) -> None:
    types = hook_types or [HOOK_TYPE_PLANNING]
    _TRUSTED_HOOKS[plugin_ref] = TrustedHook(
        plugin_ref=plugin_ref,
        factory=factory,
        hook_types=types,
    )


def list_trusted_hooks() -> list[str]:
    return sorted(_TRUSTED_HOOKS.keys())


def list_trusted_hooks_detail() -> list[dict[str, Any]]:
    return [
        {"plugin_ref": h.plugin_ref, "hook_types": h.hook_types}
        for h in sorted(_TRUSTED_HOOKS.values(), key=lambda x: x.plugin_ref)
    ]


def _merge_patch(merged: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "resolved_planning_overlay",
        "resolved_reasoning_overlay",
        "resolved_reflection_overlay",
        "pre_task_overlay",
    ):
        extra = patch.get(key)
        if extra:
            merged[key] = f"{merged.get(key, '')}\n{extra}".strip()
    weights = patch.get("resolved_action_weights")
    if isinstance(weights, dict):
        base = dict(merged.get("resolved_action_weights") or {})
        base.update({str(k): float(v) for k, v in weights.items() if v is not None})
        merged["resolved_action_weights"] = base
    extra_block = patch.get("resolved_tool_blocklist")
    if isinstance(extra_block, list):
        block = list(merged.get("resolved_tool_blocklist") or [])
        block.extend(str(t) for t in extra_block if t)
        merged["resolved_tool_blocklist"] = sorted(set(block))
    extra_allow = patch.get("resolved_tool_allowlist")
    if isinstance(extra_allow, list) and extra_allow:
        allow = list(merged.get("resolved_tool_allowlist") or [])
        if allow:
            merged["resolved_tool_allowlist"] = [t for t in allow if t in extra_allow]
        else:
            merged["resolved_tool_allowlist"] = list(extra_allow)
    presentation = patch.get("presentation_hints")
    if isinstance(presentation, dict):
        hints = dict(merged.get("presentation_hints") or {})
        hints.update(presentation)
        merged["presentation_hints"] = hints
    output_rules = patch.get("output_validation_rules")
    if isinstance(output_rules, dict):
        rules = dict(merged.get("output_validation_rules") or {})
        rules.update(output_rules)
        merged["output_validation_rules"] = rules
    return merged


def apply_trusted_hooks(
    policy: dict[str, Any],
    *,
    context: Optional[dict[str, Any]] = None,
    hook_types: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Merge declarative patches from trusted plugin_ref hooks (optional type filter)."""
    refs = list(policy.get("resolved_plugin_hooks") or [])
    if not refs:
        return policy
    merged = dict(policy)
    ctx = dict(context or {})
    allowed = set(hook_types) if hook_types else None
    for ref in refs:
        hook = _TRUSTED_HOOKS.get(str(ref))
        if not hook:
            continue
        if allowed is not None and not set(hook.hook_types) & allowed:
            continue
        merged = _merge_patch(merged, hook.patch(ctx))
    return merged


def apply_hook_stage(
    policy: dict[str, Any],
    stage: str,
    *,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return apply_trusted_hooks(policy, context=context, hook_types=[stage])
