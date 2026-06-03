"""Skill 注册表

Skill registry: builtin YAML + packages + tenant drafts at startup.
load_definition: _definitions → skill_store (tenant) → KeyError
list_definitions: merge builtin + tenant; filter by status/domain/role."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.domain.skill import SkillManifest
from app.domain.skill_models import SkillDefinition, SkillSourceType, SkillStatus
from app.services.skill_builtin_loader import load_skills_from_directory

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, SkillDefinition] = {}
        self._builtin_ids: set[str] = set()

    def register(self, definition: SkillDefinition) -> None:
        self._definitions[definition.skill_id] = definition

    def register_manifest(self, manifest: SkillManifest) -> None:
        """Backward-compatible registration from legacy SkillManifest."""
        self.register(
            SkillDefinition(
                skill_id=manifest.skill_id,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
                required_role=manifest.required_role,
                risk_level=manifest.risk_level,
                input_schema=manifest.input_schema,
                output_schema=manifest.output_schema,
                tags=manifest.tags,
                enabled=manifest.enabled,
                deprecated=manifest.deprecated,
                tool_name=manifest.tool_name,
            )
        )

    def is_builtin(self, skill_id: str) -> bool:
        return skill_id in self._builtin_ids

    def load_definition(self, skill_id: str, *, tenant_id: Optional[str] = None) -> SkillDefinition:
        if skill_id in self._definitions:
            return self._definitions[skill_id]
        from app.services.skill_store import get_skill_store
        from app.services.tenant_context import get_tenant_id

        custom = get_skill_store().load(skill_id, tenant_id or get_tenant_id())
        if custom:
            return custom
        raise KeyError(f"Unknown skill: {skill_id}")

    def list_definitions(
        self,
        *,
        tags: list[str] | None = None,
        role: str = "user",
        status: Optional[SkillStatus] = None,
        domain: Optional[str] = None,
        query: Optional[str] = None,
        include_disabled: bool = False,
        tenant_id: Optional[str] = None,
        include_custom: bool = True,
    ) -> list[SkillDefinition]:
        seen: set[str] = set()
        out: list[SkillDefinition] = []
        q = (query or "").strip().lower()
        for skill in self._definitions.values():
            if not include_disabled and not skill.enabled:
                continue
            if skill.deprecated:
                continue
            if status is not None and skill.status != status:
                continue
            elif status is None and not include_disabled:
                if skill.status not in (SkillStatus.PUBLISHED, SkillStatus.DEPRECATED):
                    continue
            if not self.role_allows(skill.required_role, role):
                continue
            if domain and skill.base_domain.lower() != domain.lower():
                if skill.category.lower() != domain.lower():
                    continue
            if tags and not set(tags) & set(skill.tags):
                continue
            if q:
                hay = f"{skill.skill_id} {skill.name} {skill.description} {skill.summary}".lower()
                if q not in hay and not any(q in t.lower() for t in skill.tags):
                    continue
            out.append(skill)
            seen.add(skill.skill_id)

        if include_custom:
            from app.services.skill_store import get_skill_store
            from app.services.tenant_context import get_tenant_id

            for skill in get_skill_store().list_custom(tenant_id or get_tenant_id(), status=status):
                if skill.skill_id in seen:
                    continue
                if not include_disabled and not skill.enabled:
                    continue
                if skill.deprecated:
                    continue
                if status is not None and skill.status != status:
                    continue
                elif status is None and not include_disabled:
                    if skill.status not in (SkillStatus.PUBLISHED, SkillStatus.DEPRECATED):
                        continue
                if not self.role_allows(skill.required_role, role):
                    continue
                if domain and skill.base_domain.lower() != domain.lower():
                    if skill.category.lower() != domain.lower():
                        continue
                if tags and not set(tags) & set(skill.tags):
                    continue
                if q:
                    hay = f"{skill.skill_id} {skill.name} {skill.description} {skill.summary}".lower()
                    if q not in hay and not any(q in t.lower() for t in skill.tags):
                        continue
                out.append(skill)
                seen.add(skill.skill_id)

        from app.services.skill_governance import is_skill_blocked_for_tenant
        from app.services.tenant_context import get_tenant_id as _get_tid

        tid = tenant_id or _get_tid()
        out = [s for s in out if not is_skill_blocked_for_tenant(s.skill_id, tid)]
        return sorted(out, key=lambda s: (s.presentation.display_order, s.skill_id))

    # --- backward-compatible manifest API ---
    def list_skills(
        self,
        *,
        tags: list[str] | None = None,
        role: str = "user",
    ) -> list[SkillManifest]:
        return [self._to_manifest(d) for d in self.list_definitions(tags=tags, role=role)]

    def load_skill(self, skill_id: str) -> SkillManifest:
        return self._to_manifest(self.load_definition(skill_id))

    def get_disclosure(self, task_context: dict[str, Any]) -> list[str]:
        role = str(task_context.get("user_role", "user"))
        task_type = str(task_context.get("task_type", ""))
        mission = task_context.get("mission") or {}
        allow_high = bool(
            role == "admin"
            or (isinstance(mission, dict) and mission.get("constraints", {}).get("allow_high_risk"))
        )
        disclosed: list[str] = []
        for skill in self.list_definitions(role=role):
            if skill.risk_level == "HIGH" and not allow_high:
                continue
            if task_type and skill.tags and task_type not in skill.tags and "general" not in skill.tags:
                continue
            disclosed.append(skill.skill_id)
        return disclosed

    def invoke(self, skill_id: str, params: dict[str, Any], *, user_role: str = "user") -> dict[str, Any]:
        definition = self.load_definition(skill_id)
        if not self.role_allows(definition.required_role, user_role):
            raise PermissionError(f"Role '{user_role}' cannot invoke skill '{skill_id}'")
        if definition.tool_name:
            from app.services.tool_registry import get_tool_registry

            result = get_tool_registry().invoke(definition.tool_name, params, user_role=user_role)
        else:
            raise ValueError(f"Skill {skill_id} has no tool_name (policy-only skill)")
        _audit_skill_invoke(definition, user_role, "ok")
        return {"skill_id": skill_id, "version": definition.version, "result": result}

    def load_from_config_dir(self, directory: str | Path | None = None) -> int:
        root = Path(directory or settings.SKILL_CONFIG_DIR)
        known_tools: list[str] | None = None
        try:
            from app.services.tool_registry import get_tool_registry

            known_tools = get_tool_registry().list_tools()
        except Exception:
            known_tools = None
        loaded, warnings = load_skills_from_directory(root, known_tools=known_tools)
        for w in warnings:
            logger.warning("skill load: %s", w)
        for definition in loaded:
            self.register(definition)
            self._builtin_ids.add(definition.skill_id)
        return len(loaded)

    def load_skill_packages(self) -> int:
        if not getattr(settings, "SKILL_PACKAGES_ENABLED", True):
            return 0
        from app.services.skill_package_loader import load_all_package_definitions

        loaded, warnings = load_all_package_definitions()
        for w in warnings:
            logger.warning("skill package: %s", w)
        count = 0
        for definition in loaded:
            if definition.skill_id in self._definitions:
                logger.warning("skip package skill %s: id already registered", definition.skill_id)
                continue
            self.register(definition)
            count += 1
        return count

    def reload(self) -> int:
        self._definitions.clear()
        self._builtin_ids.clear()
        total = self.load_from_config_dir()
        total += self.load_skill_packages()
        return total

    @staticmethod
    def role_allows(required: str, actual: str) -> bool:
        order = {"guest": 0, "user": 1, "admin": 2}
        return order.get(actual, 0) >= order.get(required, 1)

    @staticmethod
    def _to_manifest(definition: SkillDefinition) -> SkillManifest:
        return SkillManifest(
            skill_id=definition.skill_id,
            name=definition.name,
            version=definition.version,
            description=definition.description,
            required_role=definition.required_role,
            risk_level=definition.risk_level,
            input_schema=definition.input_schema,
            output_schema=definition.output_schema,
            tags=definition.tags,
            enabled=definition.enabled,
            deprecated=definition.deprecated,
            tool_name=definition.tool_name,
        )

    def categories_summary(
        self,
        *,
        role: str = "user",
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        builtin_count = 0
        tenant_count = 0
        for skill in self.list_definitions(
            role=role, tenant_id=tenant_id, include_custom=True
        ):
            counts[skill.category] = counts.get(skill.category, 0) + 1
            dom = skill.base_domain or "general"
            domain_counts[dom] = domain_counts.get(dom, 0) + 1
            if skill.source_type == SkillSourceType.TENANT:
                tenant_count += 1
            else:
                builtin_count += 1
        categories = [{"id": k, "count": v} for k, v in sorted(counts.items())]
        domains = [{"id": k, "count": v} for k, v in sorted(domain_counts.items())]
        sources = [
            {"id": "system", "count": builtin_count},
            {"id": "tenant", "count": tenant_count},
        ]
        return [
            {"type": "source", "items": sources},
            {"type": "scenario", "items": categories},
            {"type": "domain", "items": domains},
        ]


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        if settings.SKILL_ENABLED or settings.SKILL_RUNTIME_POLICY_ENABLED:
            from app.services.skill_hooks_bootstrap import bootstrap_skill_hooks

            bootstrap_skill_hooks()
            _registry.load_from_config_dir()
            _registry.load_skill_packages()
    return _registry


def reset_skill_registry() -> None:
    global _registry
    _registry = None


def _audit_skill_invoke(definition: SkillDefinition, user_role: str, status: str) -> None:
    try:
        from app.services.audit_store import get_audit_store

        get_audit_store().append_events(
            "skill-registry",
            [
                {
                    "event": "skill_invoke",
                    "skill_id": definition.skill_id,
                    "version": definition.version,
                    "user_role": user_role,
                    "status": status,
                }
            ],
        )
    except Exception:
        pass
