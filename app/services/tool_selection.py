"""两阶段工具选择
阶段 Stages: relevance score by goal/domain → risk/role filter → skill allowlist intersect.

Two-stage tool selection for planning prompt (top-k, not full registry)."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.tool_registry import ToolRegistry, ToolSpec

_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_ROLE_ORDER = {"guest": 0, "user": 1, "admin": 2}
_TOKEN_RE = re.compile(r"[a-z0-9_\u4e00-\u9fff]+", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2}


def _relevance_score(goal: str, spec: ToolSpec, domain: str) -> float:
    goal_tokens = _tokenize(goal)
    desc_tokens = _tokenize(f"{spec.name} {spec.description}")
    domain_tokens = _tokenize(domain)
    if not goal_tokens and not domain_tokens:
        return 0.1
    overlap = len(goal_tokens & desc_tokens)
    domain_overlap = len(domain_tokens & desc_tokens) | len(domain_tokens & _tokenize(spec.name))
    name_bonus = 0.3 if any(t in spec.name.lower() for t in goal_tokens) else 0.0
    return overlap * 0.25 + domain_overlap * 0.15 + name_bonus + 0.05


def _risk_compatible(task_risk: str, tool_risk: str) -> bool:
    task_level = _RISK_ORDER.get(str(task_risk or "LOW").upper(), 0)
    tool_level = _RISK_ORDER.get(str(tool_risk or "LOW").upper(), 0)
    if task_level <= _RISK_ORDER["MEDIUM"]:
        return tool_level <= _RISK_ORDER["HIGH"]
    return True


def retrieve_relevant_tools(
    goal: str,
    domain: str,
    risk_level: str,
    registry: ToolRegistry,
    *,
    top_k: int = 10,
    pack_tools: Optional[list[str]] = None,
    skill_blocklist: Optional[list[str]] = None,
) -> list[ToolSpec]:
    """Return top-k tools by goal/domain relevance and risk compatibility."""
    allowed = set(pack_tools or []) or None
    blocked = set(skill_blocklist or [])
    scored: list[tuple[float, ToolSpec]] = []
    for name in registry.list_tools():
        if name in blocked:
            continue
        if allowed is not None and name not in allowed:
            continue
        spec = registry.get(name)
        if not _risk_compatible(risk_level, spec.risk_level):
            continue
        score = _relevance_score(goal, spec, domain)
        scored.append((score, spec))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    if not scored:
        return [registry.get(n) for n in registry.list_tools()[:top_k]]
    return [spec for _, spec in scored[:top_k]]


def format_tools_for_prompt(tools: list[ToolSpec]) -> list[dict[str, str]]:
    return [
        {
            "name": spec.name,
            "description": str(spec.description or "")[:240],
            "risk_level": str(spec.risk_level or "LOW"),
        }
        for spec in tools
    ]


def validate_tool_selection(
    selected: list[str],
    params: dict[str, Any],
    registry: ToolRegistry,
    user_role: str,
) -> tuple[list[str], list[str]]:
    """Validate tool availability and required params; return (valid_tools, issues)."""
    valid: list[str] = []
    issues: list[str] = []
    for tool_name in selected or []:
        name = str(tool_name).strip()
        if not name:
            continue
        try:
            spec = registry.get(name)
        except KeyError:
            issues.append(f"tool_not_found: {name}")
            continue
        if not registry.check_permission(name, user_role):
            issues.append(f"permission_denied: {name}")
            continue
        tool_params = params.get(name, {}) if isinstance(params.get(name), dict) else {}
        properties = spec.input_schema.get("properties", {})
        for key, prop in properties.items():
            if prop.get("required") and key not in tool_params:
                if key in ("task_id", "filename") and name in (
                    "read_text_artifact",
                    "write_text_artifact",
                    "append_text_artifact",
                    "edit_text_artifact",
                    "get_runtime_info",
                ):
                    continue
                issues.append(f"missing_param: {name}.{key}")
        valid.append(name)
    return valid, issues
