"""Skill 治理
resolve_skill_for_task 首行检查 is_skill_blocked_for_tenant；admin API 写入 _governance JSON。

Catalog governance: global disabled list + per-tenant blocks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)


def _governance_root() -> Path:
    raw = getattr(settings, "SKILL_DATA_DIR", "data/skills")
    root = Path(raw)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    path = root / "_governance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("governance read failed %s: %s", path, exc)
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def is_skill_globally_disabled(skill_id: str) -> bool:
    data = _load_json(_governance_root() / "global_disabled.json")
    disabled = set(data.get("skill_ids") or [])
    return skill_id in disabled


def is_skill_blocked_for_tenant(skill_id: str, tenant_id: Optional[str]) -> bool:
    if is_skill_globally_disabled(skill_id):
        return True
    if not tenant_id:
        return False
    safe = "".join(c for c in str(tenant_id) if c.isalnum() or c in "_-")[:64]
    data = _load_json(_governance_root() / "tenant_blocks" / f"{safe}.json")
    blocked = set(data.get("skill_ids") or [])
    return skill_id in blocked


def get_governance_snapshot() -> dict[str, Any]:
    root = _governance_root()
    global_data = _load_json(root / "global_disabled.json")
    tenant_blocks: dict[str, list[str]] = {}
    blocks_dir = root / "tenant_blocks"
    if blocks_dir.is_dir():
        for path in sorted(blocks_dir.glob("*.json")):
            data = _load_json(path)
            tenant_blocks[path.stem] = list(data.get("skill_ids") or [])
    return {
        "global_disabled": list(global_data.get("skill_ids") or []),
        "global_reason": global_data.get("reason", ""),
        "tenant_blocks": tenant_blocks,
    }


def set_global_disabled(skill_ids: list[str], *, reason: str = "", operator: str = "system") -> dict[str, Any]:
    path = _governance_root() / "global_disabled.json"
    payload = {
        "skill_ids": sorted(set(skill_ids)),
        "reason": reason,
        "updated_by": operator,
    }
    _save_json(path, payload)
    _audit("global_disable", payload)
    return payload


def set_tenant_blocks(
    tenant_id: str,
    skill_ids: list[str],
    *,
    reason: str = "",
    operator: str = "system",
) -> dict[str, Any]:
    safe = "".join(c for c in str(tenant_id) if c.isalnum() or c in "_-")[:64]
    blocks_dir = _governance_root() / "tenant_blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    path = blocks_dir / f"{safe}.json"
    payload = {
        "tenant_id": tenant_id,
        "skill_ids": sorted(set(skill_ids)),
        "reason": reason,
        "updated_by": operator,
    }
    _save_json(path, payload)
    _audit("tenant_block", payload)
    return payload


def _audit(action: str, payload: dict[str, Any]) -> None:
    try:
        from app.services.audit_store import get_audit_store

        get_audit_store().append_events(
            "skill-governance",
            [{"event": f"skill_{action}", **payload}],
        )
    except Exception:
        pass
