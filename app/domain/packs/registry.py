from __future__ import annotations

import json
from typing import Any

from app.domain.packs.analysis import ANALYSIS_PACK
from app.domain.packs.base import DomainPack
from app.domain.packs.code import CODE_PACK
from app.domain.packs.document import DOCUMENT_PACK
from app.domain.packs.single_turn import SINGLE_TURN_PACK

_PACKS: dict[str, DomainPack] = {
    SINGLE_TURN_PACK.name: SINGLE_TURN_PACK,
    DOCUMENT_PACK.name: DOCUMENT_PACK,
    CODE_PACK.name: CODE_PACK,
    ANALYSIS_PACK.name: ANALYSIS_PACK,
}


def list_domain_packs() -> list[DomainPack]:
    return list(_PACKS.values())


def get_domain_pack(name: str) -> DomainPack:
    key = name.lower().strip()
    if key not in _PACKS:
        raise KeyError(f"Unknown domain pack: {name}")
    return _PACKS[key]


def resolve_mission_pack(
    *,
    mission_kind: str | None = None,
    task_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DomainPack:
    """Pick pack from explicit mission.kind / mission_kind / task_type only."""
    payload = payload or {}
    block = payload.get("mission") if isinstance(payload.get("mission"), dict) else {}
    kind = (
        (mission_kind or "").strip()
        or str(block.get("kind", "")).strip()
        or str(payload.get("mission_kind", "")).strip()
    ).lower()

    if kind and kind in _PACKS:
        return _PACKS[kind]

    task = (task_type or "").lower()
    if task in _PACKS:
        return _PACKS[task]

    return SINGLE_TURN_PACK


def list_pack_names() -> list[str]:
    return list(_PACKS.keys())


def build_worker_catalog(allowed: list[str] | None = None) -> list[dict[str, Any]]:
    """Structured worker list for supervisor LLM routing (no keyword inference)."""
    names = allowed if allowed else list_pack_names()
    catalog: list[dict[str, Any]] = []
    for name in names:
        key = name.lower().strip()
        if key not in _PACKS:
            continue
        pack = _PACKS[key]
        capabilities = pack.metadata.get("capabilities") or [pack.name]
        catalog.append(
            {
                "domain": pack.name,
                "description": pack.description,
                "tools": pack.tools,
                "risk_level": pack.risk_level,
                "capabilities": list(capabilities),
            }
        )
    return catalog


def build_supervisor_decompose_system_prompt(allowed: list[str] | None = None) -> str:
    catalog = build_worker_catalog(allowed)
    return (
        "You are a supervisor agent. Split the user goal into 1-3 subtasks.\n"
        "Choose domain for each subtask ONLY from the worker catalog below.\n"
        f"Worker catalog: {json.dumps(catalog, ensure_ascii=False)}\n"
        'Return ONE JSON object: {"subtasks":[{"domain":"<name from catalog>",'
        '"description":"<concrete subtask for that worker>"}]}\n'
        "No markdown fences. Use the user's language in descriptions."
    )
