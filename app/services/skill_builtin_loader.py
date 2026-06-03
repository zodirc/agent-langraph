"""内置 Skill YAML 加载
启动链 Startup (get_skill_registry 单例初始化)
    → parse_skill_yaml  # overlays, allowed_tools, presentation.input_form_schema
    → validate_skill_definition(known_tools=registry.list_tools())
  → SkillRegistry.register
运行时解析 Runtime resolve (skill_registry.load_definition)
  内存命中 → 返回 SkillDefinition
  未命中 → skill_store.load(tenant) 租户 YAML
  仍无 → KeyError → skill_resolver.SkillNotFoundError
与 attach_skill_to_payload 关系: definition → build_runtime_policy → _skill_policy

Builtin skill YAML loader.
  load_skills_from_directory(config/skills)
  load_all_package_definitions()  # skill_package_loader
_builtin_ids"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.domain.skill_models import (
    SkillDefinition,
    SkillOwnerType,
    SkillPresentation,
    SkillSourceType,
    SkillStatus,
    SkillVisibility,
)
from app.services.skill_validator import validate_skill_definition

logger = logging.getLogger(__name__)


def _coerce_enum(value: Any, enum_cls: type, default: Any) -> Any:
    if value is None:
        return default
    raw = str(value).lower().strip()
    for member in enum_cls:
        if member.value == raw or member.name.lower() == raw:
            return member
    return default


def parse_skill_yaml(data: dict[str, Any]) -> SkillDefinition:
    """单文件 YAML → SkillDefinition；overlay/allowed_tools 供 runtime policy 使用。"""
    pres_raw = data.get("presentation") or {}
    if not isinstance(pres_raw, dict):
        pres_raw = {}
    presentation = SkillPresentation(
        icon=pres_raw.get("icon"),
        hero_description=pres_raw.get("hero_description"),
        usage_notes=pres_raw.get("usage_notes"),
        display_order=int(pres_raw.get("display_order", 0) or 0),
        example_prompts=list(pres_raw.get("example_prompts") or []),
        input_form_schema=pres_raw.get("input_form_schema") or {},
        output_preview_schema=pres_raw.get("output_preview_schema") or {},
        badges=list(pres_raw.get("badges") or []),
    )
    return SkillDefinition(
        skill_id=str(data["skill_id"]),
        slug=str(data.get("slug") or data["skill_id"]),
        name=str(data.get("name", data["skill_id"])),
        version=str(data.get("version", "1.0.0")),
        description=str(data.get("description", "")),
        summary=str(data.get("summary", data.get("description", ""))),
        source_type=_coerce_enum(data.get("source_type"), SkillSourceType, SkillSourceType.BUILTIN),
        owner_type=_coerce_enum(data.get("owner_type"), SkillOwnerType, SkillOwnerType.SYSTEM),
        owner_id=str(data.get("owner_id", "system")),
        base_domain=str(data.get("base_domain", "single_turn")),
        applicable_task_types=list(data.get("applicable_task_types") or []),
        preferred_execution_mode=str(data.get("preferred_execution_mode", "single")),
        allowed_tools=list(data.get("allowed_tools") or []),
        blocked_tools=list(data.get("blocked_tools") or []),
        planning_overlay=str(data.get("planning_overlay") or ""),
        reasoning_overlay=str(data.get("reasoning_overlay") or ""),
        reflection_overlay=str(data.get("reflection_overlay") or ""),
        action_policy=data.get("action_policy") or {},
        output_contract=data.get("output_contract") or {},
        examples=list(data.get("examples") or []),
        tags=list(data.get("tags") or []),
        category=str(data.get("category", "general")),
        risk_level=str(data.get("risk_level", "LOW")),
        required_role=str(data.get("required_role", "user")),
        enabled=bool(data.get("enabled", True)),
        visibility=_coerce_enum(
            data.get("visibility"), SkillVisibility, SkillVisibility.SYSTEM_PUBLIC
        ),
        status=_coerce_enum(data.get("status"), SkillStatus, SkillStatus.PUBLISHED),
        plugin_ref=data.get("plugin_ref"),
        presentation=presentation,
        tool_name=data.get("tool_name"),
        input_schema=data.get("input_schema") or {},
        output_schema=data.get("output_schema") or {},
        deprecated=bool(data.get("deprecated", False)),
    )


def load_skills_from_directory(
    directory: str | Path,
    *,
    known_tools: list[str] | None = None,
    strict: bool = False,
) -> tuple[list[SkillDefinition], list[str]]:
    """扫描目录下 *.yaml；strict=True 时跳过校验失败文件。

    Scan *.yaml skill definitions; strict mode skips invalid files.
    """
    root = Path(directory)
    if not root.is_dir():
        return [], []
    loaded: list[SkillDefinition] = []
    warnings: list[str] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict) or "skill_id" not in data:
                warnings.append(f"{path.name}: missing skill_id")
                continue
            definition = parse_skill_yaml(data)
            issues = validate_skill_definition(definition, known_tools=known_tools)
            if issues:
                msg = f"{path.name}: " + "; ".join(issues)
                if strict:
                    warnings.append(msg)
                    continue
                logger.warning("skill validation: %s", msg)
            loaded.append(definition)
        except Exception as exc:
            warnings.append(f"{path.name}: {exc}")
            logger.warning("skip skill file %s: %s", path, exc)
    return loaded, warnings
