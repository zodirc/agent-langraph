"""Skill platform domain models (Skill-Driven Agent Platform)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class SkillSourceType(str, Enum):
    BUILTIN = "builtin"
    TENANT = "tenant"
    SYSTEM_PACKAGE = "system_package"


class SkillOwnerType(str, Enum):
    SYSTEM = "system"
    TENANT = "tenant"
    USER = "user"


class SkillVisibility(str, Enum):
    SYSTEM_PUBLIC = "system_public"
    TENANT_PRIVATE = "tenant_private"
    TENANT_SHARED = "tenant_shared"


class SkillStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class SkillPresentation(BaseModel):
    icon: Optional[str] = None
    hero_description: Optional[str] = None
    usage_notes: Optional[str] = None
    display_order: int = 0
    example_prompts: list[str] = Field(default_factory=list)
    input_form_schema: dict[str, Any] = Field(default_factory=dict)
    output_preview_schema: dict[str, Any] = Field(default_factory=dict)
    badges: list[str] = Field(default_factory=list)


class SkillDefinition(BaseModel):
    """Full skill definition — builtin, tenant, or future package."""

    skill_id: str
    slug: str = ""
    name: str
    version: str = "1.0.0"
    description: str = ""
    summary: str = ""
    source_type: SkillSourceType = SkillSourceType.BUILTIN
    owner_type: SkillOwnerType = SkillOwnerType.SYSTEM
    owner_id: str = "system"
    base_domain: str = "single_turn"
    applicable_task_types: list[str] = Field(default_factory=list)
    preferred_execution_mode: str = "single"
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    planning_overlay: str = ""
    reasoning_overlay: str = ""
    reflection_overlay: str = ""
    action_policy: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    risk_level: str = "LOW"
    required_role: str = "user"
    enabled: bool = True
    visibility: SkillVisibility = SkillVisibility.SYSTEM_PUBLIC
    status: SkillStatus = SkillStatus.PUBLISHED
    plugin_ref: Optional[str] = None
    presentation: SkillPresentation = Field(default_factory=SkillPresentation)
    # Legacy invoke path — policy-only skills omit tool_name
    tool_name: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    deprecated: bool = False

    @model_validator(mode="after")
    def _fill_slug(self) -> SkillDefinition:
        if not self.slug:
            self.slug = self.skill_id.replace("_", "-")
        return self

    def to_snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def is_usable_by_task(self) -> bool:
        return self.enabled and self.status == SkillStatus.PUBLISHED and not self.deprecated


class SkillRuntimePolicy(BaseModel):
    """Resolved policy consumed by runtime graph and prompt system."""

    skill_id: str
    version: str
    resolved_domain: str
    resolved_execution_mode_hint: str
    resolved_tool_allowlist: list[str] = Field(default_factory=list)
    resolved_tool_blocklist: list[str] = Field(default_factory=list)
    resolved_planning_overlay: str = ""
    resolved_reasoning_overlay: str = ""
    resolved_reflection_overlay: str = ""
    resolved_action_weights: dict[str, float] = Field(default_factory=dict)
    resolved_output_contract: dict[str, Any] = Field(default_factory=dict)
    resolved_plugin_hooks: list[str] = Field(default_factory=list)
    source_type: str = "builtin"
    owner_type: str = "system"
    owner_id: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SkillVersionRecord(BaseModel):
    skill_id: str
    version: str
    definition_snapshot: dict[str, Any] = Field(default_factory=dict)
    change_log: str = ""
    status: SkillStatus = SkillStatus.PUBLISHED
    published_by: Optional[str] = None
    published_at: Optional[str] = None
