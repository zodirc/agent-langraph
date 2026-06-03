"""Register built-in trusted skill hooks at application startup."""

from __future__ import annotations

from typing import Any

from app.services.skill_hooks import (
    HOOK_TYPE_CANDIDATE_SCORE,
    HOOK_TYPE_OUTPUT_VALIDATE,
    HOOK_TYPE_PLANNING,
    HOOK_TYPE_PRE_TASK,
    HOOK_TYPE_PRESENTATION,
    HOOK_TYPE_TOOL_FILTER,
    register_trusted_hook,
)


def _code_review_security_patch(_ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolved_planning_overlay": (
            "[Trusted hook: code_review_security_v1] "
            "Prioritize OWASP-style risks, secrets in code, and unsafe defaults."
        ),
        "resolved_reasoning_overlay": (
            "Tag findings by severity: Critical / High / Medium / Low."
        ),
        "resolved_tool_blocklist": ["calculator"],
    }


def _financial_analysis_patch(_ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolved_planning_overlay": (
            "[Trusted hook: financial_analysis_v1] "
            "Use conservative assumptions; separate facts from estimates."
        ),
    }


def _pre_task_defaults_patch(ctx: dict[str, Any]) -> dict[str, Any]:
    skill_id = str(ctx.get("skill_id") or "")
    return {
        "pre_task_overlay": f"[pre_task_hook] skill={skill_id} policy snapshot applied.",
        "presentation_hints": {"pre_task_ready": True},
    }


def bootstrap_skill_hooks() -> int:
    register_trusted_hook(
        "builtin.skill_hooks.code_review_security_v1",
        _code_review_security_patch,
        hook_types=[
            HOOK_TYPE_PLANNING,
            HOOK_TYPE_TOOL_FILTER,
            HOOK_TYPE_OUTPUT_VALIDATE,
        ],
    )
    register_trusted_hook(
        "enterprise.skill_hooks.financial_analysis_v1",
        _financial_analysis_patch,
        hook_types=[HOOK_TYPE_PLANNING],
    )
    register_trusted_hook(
        "builtin.skill_hooks.pre_task_defaults_v1",
        _pre_task_defaults_patch,
        hook_types=[HOOK_TYPE_PRE_TASK, HOOK_TYPE_PRESENTATION],
    )
    from app.services.skill_hooks import list_trusted_hooks

    return len(list_trusted_hooks())
