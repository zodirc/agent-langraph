"""Load enterprise/system skill packages from config/skill_packages/."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import settings
from app.domain.skill_models import SkillDefinition, SkillSourceType, SkillStatus
from app.services.skill_builtin_loader import load_skills_from_directory

logger = logging.getLogger(__name__)


def skill_packages_root() -> Path:
    raw = getattr(settings, "SKILL_PACKAGES_DIR", "config/skill_packages")
    root = Path(raw)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root


def list_installed_packages() -> list[dict[str, Any]]:
    root = skill_packages_root()
    if not root.is_dir():
        return []
    packages: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                entry = dict(data)
                entry["path"] = manifest_path.parent.name
                packages.append(entry)
        except Exception as exc:
            logger.warning("skip package manifest %s: %s", manifest_path, exc)
    return packages


def load_package_skills(package_dir: Path) -> tuple[list[SkillDefinition], list[str]]:
    manifest_path = package_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return [], [f"{package_dir.name}: missing manifest.yaml"]
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        return [], [f"{package_dir.name}: invalid manifest"]
    skills_rel = str(manifest.get("skills_dir", "skills"))
    skills_dir = package_dir / skills_rel
    package_id = str(manifest.get("package_id", package_dir.name))
    loaded_defs, warnings = load_skills_from_directory(skills_dir)
    out: list[SkillDefinition] = []
    for defn in loaded_defs:
        out.append(
            defn.model_copy(
                update={
                    "source_type": SkillSourceType.SYSTEM_PACKAGE,
                    "owner_id": str(manifest.get("publisher", "system")),
                    "status": SkillStatus.PUBLISHED,
                    "tags": list(set(defn.tags + [f"package:{package_id}"])),
                }
            )
        )
    return out, warnings


def load_all_package_definitions() -> tuple[list[SkillDefinition], list[str]]:
    root = skill_packages_root()
    if not root.is_dir():
        return [], []
    all_defs: list[SkillDefinition] = []
    warnings: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        defs, warns = load_package_skills(child)
        all_defs.extend(defs)
        warnings.extend(warns)
    return all_defs, warnings
