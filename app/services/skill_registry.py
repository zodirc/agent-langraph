"""Skill manifest registry with progressive disclosure."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from app.config.settings import settings
from app.domain.skill import SkillManifest

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(self, manifest: SkillManifest) -> None:
        self._skills[manifest.skill_id] = manifest

    def list_skills(
        self,
        *,
        tags: list[str] | None = None,
        role: str = "user",
    ) -> list[SkillManifest]:
        out: list[SkillManifest] = []
        for skill in self._skills.values():
            if not skill.enabled or skill.deprecated:
                continue
            if not self._role_allows(skill.required_role, role):
                continue
            if tags and not set(tags) & set(skill.tags):
                continue
            out.append(skill)
        return sorted(out, key=lambda s: s.skill_id)

    def load_skill(self, skill_id: str) -> SkillManifest:
        if skill_id not in self._skills:
            raise KeyError(f"Unknown skill: {skill_id}")
        return self._skills[skill_id]

    def get_disclosure(self, task_context: dict[str, Any]) -> list[str]:
        """Return skill_ids appropriate for the current task context."""
        role = str(task_context.get("user_role", "user"))
        task_type = str(task_context.get("task_type", ""))
        mission = task_context.get("mission") or {}
        allow_high = bool(
            role == "admin"
            or (isinstance(mission, dict) and mission.get("constraints", {}).get("allow_high_risk"))
        )
        disclosed: list[str] = []
        for skill in self._skills.values():
            if not skill.enabled or skill.deprecated:
                continue
            if not self._role_allows(skill.required_role, role):
                continue
            if skill.risk_level == "HIGH" and not allow_high:
                continue
            if task_type and skill.tags and task_type not in skill.tags and "general" not in skill.tags:
                continue
            disclosed.append(skill.skill_id)
        return disclosed

    def invoke(self, skill_id: str, params: dict[str, Any], *, user_role: str = "user") -> dict[str, Any]:
        skill = self.load_skill(skill_id)
        if not self._role_allows(skill.required_role, user_role):
            raise PermissionError(f"Role '{user_role}' cannot invoke skill '{skill_id}'")
        if skill.handler:
            result = skill.handler(params)
        elif skill.tool_name:
            from app.services.tool_registry import get_tool_registry

            result = get_tool_registry().invoke(skill.tool_name, params, user_role=user_role)
        else:
            raise ValueError(f"Skill {skill_id} has no handler or tool_name")
        _audit_skill_invoke(skill, user_role, "ok")
        return {"skill_id": skill_id, "version": skill.version, "result": result}

    def load_from_config_dir(self, directory: str | Path | None = None) -> int:
        root = Path(directory or settings.SKILL_CONFIG_DIR)
        if not root.is_dir():
            return 0
        count = 0
        for path in sorted(root.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                manifest = SkillManifest(
                    skill_id=str(data["skill_id"]),
                    name=str(data.get("name", data["skill_id"])),
                    version=str(data.get("version", "1.0.0")),
                    description=str(data.get("description", "")),
                    required_role=str(data.get("required_role", "user")),
                    risk_level=str(data.get("risk_level", "LOW")),
                    input_schema=data.get("input_schema") or {},
                    output_schema=data.get("output_schema") or {},
                    tags=list(data.get("tags") or []),
                    enabled=bool(data.get("enabled", True)),
                    deprecated=bool(data.get("deprecated", False)),
                    tool_name=data.get("tool_name"),
                )
                self.register(manifest)
                count += 1
            except Exception as exc:
                logger.warning("skip skill file %s: %s", path, exc)
        return count

    def reload(self) -> int:
        self._skills.clear()
        return self.load_from_config_dir()

    @staticmethod
    def _role_allows(required: str, actual: str) -> bool:
        order = {"guest": 0, "user": 1, "admin": 2}
        return order.get(actual, 0) >= order.get(required, 1)


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        if settings.SKILL_ENABLED:
            _registry.load_from_config_dir()
    return _registry


def reset_skill_registry() -> None:
    global _registry
    _registry = None


def _audit_skill_invoke(skill: SkillManifest, user_role: str, status: str) -> None:
    try:
        from app.services.audit_store import get_audit_store

        get_audit_store().append_events(
            "skill-registry",
            [
                {
                    "event": "skill_invoke",
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "user_role": user_role,
                    "status": status,
                }
            ],
        )
    except Exception:
        pass
