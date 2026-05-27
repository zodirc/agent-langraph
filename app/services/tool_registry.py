from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_role: str
    risk_level: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        from app.services.builtin_tools import register_builtin_tools

        register_builtin_tools(self)

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def check_permission(self, tool_name: str, user_role: str) -> bool:
        spec = self.get(tool_name)
        role_order = {"guest": 0, "user": 1, "admin": 2}
        required = role_order.get(spec.required_role, 1)
        actual = role_order.get(user_role, 0)
        return actual >= required

    def invoke(self, tool_name: str, params: dict[str, Any], user_role: str = "user") -> dict[str, Any]:
        if not self.check_permission(tool_name, user_role):
            raise PermissionError(f"Role '{user_role}' cannot invoke '{tool_name}'")
        spec = self.get(tool_name)
        validated = _validate_params(params, spec.input_schema)
        result = spec.handler(validated)
        return {"tool": tool_name, "result": result, "risk_level": spec.risk_level}


def _validate_params(params: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    validated: dict[str, Any] = {}
    for key, spec in properties.items():
        if key in params:
            validated[key] = params[key]
        elif spec.get("required"):
            raise ValueError(f"Missing required parameter: {key}")
    validated.update({k: v for k, v in params.items() if k not in validated})
    return validated


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
