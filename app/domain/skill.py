"""Skill manifest domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class SkillManifest:
    skill_id: str
    name: str
    version: str
    description: str
    required_role: str = "user"
    risk_level: str = "LOW"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    deprecated: bool = False
    handler: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None
    tool_name: Optional[str] = None
