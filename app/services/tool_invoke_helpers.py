"""Shared tool invoke helpers (rm_path preview auto-commit, etc.)."""

from __future__ import annotations

from typing import Any


def invoke_tool_with_guards(
    tool_name: str,
    params: dict[str, Any],
    *,
    user_role: str = "user",
) -> dict[str, Any]:
    """Invoke a registry tool; rm_path auto-runs dry_run when preview_token omitted."""
    from app.services.tool_registry import get_tool_registry

    registry = get_tool_registry()
    name = str(tool_name or "").strip()
    call_params = dict(params)
    if (
        name == "rm_path"
        and not bool(call_params.get("dry_run"))
        and not str(call_params.get("preview_token") or "").strip()
    ):
        dry_wrap = registry.invoke(name, {**call_params, "dry_run": True}, user_role=user_role)
        token = str((dry_wrap.get("result") or {}).get("preview_token") or "").strip()
        if not token:
            raise ValueError("rm_path dry_run did not return preview_token")
        call_params = {**call_params, "dry_run": False, "preview_token": token}
    return registry.invoke(name, call_params, user_role=user_role)


__all__ = ["invoke_tool_with_guards"]
