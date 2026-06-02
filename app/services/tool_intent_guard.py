"""
Tool intent guard — structured safety checks before tool invocation.
"""

from __future__ import annotations

from typing import Any

from app.services.tool_registry import ToolSpec

INJECTION_MARKERS = [
    "ignore previous instructions",
    "忽略之前的指令",
    "system prompt",
    "你是一个",
    "assistant:",
    "<|im_start|>",
    "disregard all",
    "forget your instructions",
]

_ROLE_ORDER = {"guest": 0, "user": 1, "admin": 2}
_HIGH_RISK_TOOLS = {"rm_path"}


def check_tool_params_safe(
    tool_name: str,
    params: dict[str, Any],
    spec: ToolSpec,
    user_goal: str,
    *,
    user_role: str = "user",
) -> tuple[bool, list[str]]:
    """Return (safe, issues). Blocks path escape and prompt-injection markers."""
    issues: list[str] = []
    goal_text = str(user_goal or "").lower()

    required_level = _ROLE_ORDER.get(str(spec.required_role or "user"), 1)
    actual_level = _ROLE_ORDER.get(str(user_role or "user"), 0)
    if actual_level < required_level:
        issues.append(
            f"permission_denied: role '{user_role}' cannot invoke '{tool_name}' "
            f"(requires {spec.required_role})"
        )

    tool_risk = str(spec.risk_level or "LOW").upper()
    if tool_risk in ("HIGH", "CRITICAL") and actual_level < _ROLE_ORDER["admin"]:
        issues.append(
            f"high_risk_tool: '{tool_name}' requires admin or human review"
        )

    if tool_name in _HIGH_RISK_TOOLS:
        for marker in INJECTION_MARKERS:
            if marker.lower() in goal_text:
                issues.append(f"injection_marker_in_goal_for_{tool_name}: {marker}")

    for key, value in (params or {}).items():
        text = str(value).lower()
        for marker in INJECTION_MARKERS:
            if marker.lower() in text:
                issues.append(f"injection_marker_in_{tool_name}.{key}: {marker}")

        key_lower = str(key).lower()
        if "path" in key_lower or "file" in key_lower:
            raw = str(value)
            if ".." in raw:
                issues.append(f"path_escape_in_{tool_name}.{key}: {value}")
            elif raw.startswith("/") and not raw.startswith("/tmp"):
                issues.append(f"absolute_path_in_{tool_name}.{key}: {value}")

    return len(issues) == 0, issues
