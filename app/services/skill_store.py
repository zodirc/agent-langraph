"""Persistent store for tenant/user custom skills (YAML + version snapshots)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from app.config.settings import settings
from app.domain.skill_models import (
    SkillDefinition,
    SkillOwnerType,
    SkillSourceType,
    SkillStatus,
    SkillVersionRecord,
    SkillVisibility,
)
from app.services.skill_builtin_loader import parse_skill_yaml
from app.services.skill_validator import validate_skill_definition
from app.services.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)

_SKILL_ID_SAFE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _tenant_key(tenant_id: Optional[str] = None) -> str:
    tid = tenant_id if tenant_id is not None else get_tenant_id()
    if not tid:
        return "default"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(tid))[:64]
    return safe or "default"


def skill_data_root() -> Path:
    raw = getattr(settings, "SKILL_DATA_DIR", "data/skills")
    root = Path(raw)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root


def tenant_skills_dir(tenant_id: Optional[str] = None) -> Path:
    path = skill_data_root() / _tenant_key(tenant_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def versions_dir(skill_id: str, tenant_id: Optional[str] = None) -> Path:
    path = tenant_skills_dir(tenant_id) / "versions" / skill_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _skill_path(skill_id: str, tenant_id: Optional[str] = None) -> Path:
    return tenant_skills_dir(tenant_id) / f"{skill_id}.yaml"


def normalize_skill_id(raw: str) -> str:
    sid = re.sub(r"[^a-z0-9_]", "_", str(raw).lower().strip())
    sid = sid.strip("_")
    if not _SKILL_ID_SAFE.match(sid):
        raise ValueError(
            "skill_id must be 3-64 chars, start with a letter, use lowercase letters, digits, underscore"
        )
    return sid


class SkillStore:
    def list_custom(
        self,
        tenant_id: Optional[str] = None,
        *,
        owner_id: Optional[str] = None,
        status: Optional[SkillStatus] = None,
    ) -> list[SkillDefinition]:
        out: list[SkillDefinition] = []
        for path in sorted(tenant_skills_dir(tenant_id).glob("*.yaml")):
            try:
                defn = self._load_file(path)
            except Exception as exc:
                logger.warning("skip custom skill %s: %s", path, exc)
                continue
            if owner_id and defn.owner_id != owner_id:
                continue
            if status is not None and defn.status != status:
                continue
            out.append(defn)
        return out

    def load(self, skill_id: str, tenant_id: Optional[str] = None) -> Optional[SkillDefinition]:
        path = _skill_path(skill_id, tenant_id)
        if not path.is_file():
            return None
        return self._load_file(path)

    def save_draft(
        self,
        definition: SkillDefinition,
        *,
        tenant_id: Optional[str] = None,
        operator: str = "system",
    ) -> SkillDefinition:
        path = _skill_path(definition.skill_id, tenant_id)
        before: Optional[dict[str, Any]] = None
        if path.is_file():
            before = self._load_file(path).model_dump(mode="json")
            existing = self._load_file(path)
            if existing.status == SkillStatus.PUBLISHED:
                definition.version = existing.version
        definition.status = SkillStatus.DRAFT
        self._write_definition(
            path, definition, operator=operator, action="save_draft", before_snapshot=before
        )
        return definition

    def publish(
        self,
        skill_id: str,
        *,
        tenant_id: Optional[str] = None,
        operator: str = "system",
        change_log: str = "",
    ) -> SkillDefinition:
        path = _skill_path(skill_id, tenant_id)
        if not path.is_file():
            raise KeyError(f"Skill not found: {skill_id}")
        before = self._load_file(path).model_dump(mode="json")
        defn = self._load_file(path)
        from app.services.tool_registry import get_tool_registry

        issues = validate_skill_definition(defn, known_tools=get_tool_registry().list_tools())
        if issues:
            raise ValueError("; ".join(issues))

        was_published = defn.status == SkillStatus.PUBLISHED
        self._archive_version(defn, tenant_id=tenant_id, operator=operator, change_log=change_log)
        if was_published:
            parts = defn.version.split(".")
            try:
                patch = int(parts[-1]) + 1
                base = ".".join(parts[:-1]) if len(parts) > 1 else "1.0"
                defn.version = f"{base}.{patch}"
            except ValueError:
                defn.version = "1.0.1"
        defn.status = SkillStatus.PUBLISHED
        defn.enabled = True
        self._write_definition(
            path, defn, operator=operator, action="publish", before_snapshot=before
        )
        return defn

    def archive(self, skill_id: str, *, tenant_id: Optional[str] = None, operator: str = "system") -> SkillDefinition:
        path = _skill_path(skill_id, tenant_id)
        if not path.is_file():
            raise KeyError(skill_id)
        before = self._load_file(path).model_dump(mode="json")
        defn = self._load_file(path)
        defn.status = SkillStatus.ARCHIVED
        defn.enabled = False
        self._write_definition(
            path,
            defn,
            operator=operator,
            action="archive",
            before_snapshot=before,
        )
        return defn

    def disable(self, skill_id: str, *, tenant_id: Optional[str] = None, operator: str = "system") -> SkillDefinition:
        path = _skill_path(skill_id, tenant_id)
        if not path.is_file():
            raise KeyError(skill_id)
        defn = self._load_file(path)
        defn.status = SkillStatus.DISABLED
        defn.enabled = False
        self._write_definition(path, defn, operator=operator, action="disable")
        return defn

    def delete(self, skill_id: str, *, tenant_id: Optional[str] = None) -> bool:
        path = _skill_path(skill_id, tenant_id)
        if not path.is_file():
            return False
        path.unlink()
        vdir = versions_dir(skill_id, tenant_id)
        if vdir.is_dir():
            for child in vdir.glob("*.json"):
                child.unlink()
        return True

    def clone_from(
        self,
        source: SkillDefinition,
        *,
        new_skill_id: str,
        tenant_id: Optional[str] = None,
        operator: str = "system",
        owner_id: Optional[str] = None,
    ) -> SkillDefinition:
        tid = owner_id or _tenant_key(tenant_id)
        clone = source.model_copy(deep=True)
        clone.skill_id = normalize_skill_id(new_skill_id)
        clone.slug = clone.skill_id.replace("_", "-")
        clone.source_type = SkillSourceType.TENANT
        clone.owner_type = SkillOwnerType.TENANT
        clone.owner_id = tid
        clone.status = SkillStatus.DRAFT
        clone.enabled = True
        clone.version = "1.0.0"
        clone.visibility = SkillVisibility.TENANT_PRIVATE
        path = _skill_path(clone.skill_id, tenant_id)
        if path.is_file():
            raise ValueError(f"Skill already exists: {clone.skill_id}")
        self._write_definition(path, clone, operator=operator, action="clone")
        return clone

    def list_versions(self, skill_id: str, tenant_id: Optional[str] = None) -> list[SkillVersionRecord]:
        records: list[SkillVersionRecord] = []
        for path in sorted(versions_dir(skill_id, tenant_id).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(SkillVersionRecord.model_validate(data))
            except Exception as exc:
                logger.warning("skip version %s: %s", path, exc)
        return sorted(records, key=lambda r: r.version, reverse=True)

    def rollback(
        self,
        skill_id: str,
        version: str,
        *,
        tenant_id: Optional[str] = None,
        operator: str = "system",
    ) -> SkillDefinition:
        vpath = versions_dir(skill_id, tenant_id) / f"{version}.json"
        if not vpath.is_file():
            raise KeyError(f"Version not found: {version}")
        record = SkillVersionRecord.model_validate(json.loads(vpath.read_text(encoding="utf-8")))
        snap = record.definition_snapshot
        defn = SkillDefinition.model_validate(snap)
        defn.status = SkillStatus.PUBLISHED
        defn.enabled = True
        self._write_definition(
            _skill_path(skill_id, tenant_id),
            defn,
            operator=operator,
            action="rollback",
            extra_audit={"rollback_to": version},
        )
        return defn

    def _archive_version(
        self,
        defn: SkillDefinition,
        *,
        tenant_id: Optional[str],
        operator: str,
        change_log: str,
    ) -> None:
        record = SkillVersionRecord(
            skill_id=defn.skill_id,
            version=defn.version,
            definition_snapshot=defn.model_dump(mode="json"),
            change_log=change_log,
            status=defn.status,
            published_by=operator,
            published_at=datetime.now(timezone.utc).isoformat(),
        )
        vpath = versions_dir(defn.skill_id, tenant_id) / f"{defn.version}.json"
        vpath.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def _load_file(self, path: Path) -> SkillDefinition:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("invalid skill file")
        return parse_skill_yaml(data)

    def _write_definition(
        self,
        path: Path,
        definition: SkillDefinition,
        *,
        operator: str,
        action: str,
        extra_audit: Optional[dict[str, Any]] = None,
        before_snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        payload = definition.model_dump(mode="json")
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        self._audit_store(
            action,
            definition,
            operator,
            extra_audit,
            before_snapshot=before_snapshot,
            after_snapshot=payload,
        )

    def _audit_store(
        self,
        action: str,
        definition: SkillDefinition,
        operator: str,
        extra: Optional[dict[str, Any]] = None,
        *,
        before_snapshot: Optional[dict[str, Any]] = None,
        after_snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            from app.services.audit_store import get_audit_store

            event: dict[str, Any] = {
                "event": f"skill_{action}",
                "skill_id": definition.skill_id,
                "version": definition.version,
                "operator": operator,
                "tenant_id": definition.owner_id,
                "status": definition.status.value,
            }
            if before_snapshot is not None:
                event["before"] = before_snapshot
            if after_snapshot is not None:
                event["after"] = after_snapshot
            if extra:
                event.update(extra)
            get_audit_store().append_events("skill-store", [event])
        except Exception:
            pass


_store: SkillStore | None = None


def get_skill_store() -> SkillStore:
    global _store
    if _store is None:
        _store = SkillStore()
    return _store


def reset_skill_store() -> None:
    global _store
    _store = None
