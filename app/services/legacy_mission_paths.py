"""Legacy mission writing path manifest and audit counters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, append_audit

LEGACY_MISSION_PATHS: frozenset[str] = frozenset(
    {
        "writing_llm_decide",
        "mission_act.inline_writing_phase",
        "pipeline_reasoning_writing_fallback",
        "manuscript_without_fact_bundle",
    }
)

_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "legacy_mission_manifest.yaml"
)


def load_legacy_manifest() -> list[str]:
    if not _MANIFEST_PATH.exists():
        return sorted(LEGACY_MISSION_PATHS)
    try:
        import yaml

        raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        paths = raw.get("legacy_mission_paths") or []
        return [str(p) for p in paths]
    except Exception:
        return sorted(LEGACY_MISSION_PATHS)


def legacy_writing_path_allowed() -> bool:
    return bool(getattr(settings, "MISSION_ALLOW_LEGACY_WRITING_PATH", False))


def record_legacy_mission_path(
    path_id: str,
    *,
    state: AgentState | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if path_id not in LEGACY_MISSION_PATHS and path_id not in load_legacy_manifest():
        return
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_legacy_mission_path(path_id)
    if state is None:
        return
    append_audit(
        state,
        "legacy_mission_path",
        path_id,
        {
            "path_id": path_id,
            "allowed": legacy_writing_path_allowed(),
            **(detail or {}),
        },
    )


def block_legacy_writing_path(path_id: str) -> bool:
    """True when path must not execute (hard-off)."""
    if legacy_writing_path_allowed():
        return False
    record_legacy_mission_path(path_id, detail={"blocked": True})
    return True
