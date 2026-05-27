"""Load confirmation gate policies from settings (config.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class PreviewDefault:
    mode: str = "head"
    max_chars: int = 4500


@dataclass(frozen=True)
class ConfirmationGatesConfig:
    enabled: bool = True
    material_intervention_actions: frozenset[str] = frozenset(
        {
            "rewrite_outline",
            "reset_body",
            "edit_plot",
            "review_outline",
            "enqueue_work",
        }
    )
    outcome_work_item_kinds: frozenset[str] = frozenset(
        {
            "write_outline",
            "edit_plot",
            "reset_body",
            "append_body",
            "append_chapter",
            "write_body",
        }
    )
    intervention_watch_actions: frozenset[str] = frozenset(
        {
            "rewrite_outline",
            "reset_body",
            "edit_plot",
        }
    )
    preview_defaults: dict[str, PreviewDefault] = field(default_factory=dict)
    cancel_on_intervention: dict[str, frozenset[str]] = field(default_factory=dict)
    excerpt_max_chars: int = 4500
    delta_max_chars: int = 2000


_DEFAULT_PREVIEW: dict[str, PreviewDefault] = {
    "write_outline": PreviewDefault(mode="full", max_chars=4500),
    "edit_plot": PreviewDefault(mode="range", max_chars=4500),
    "reset_body": PreviewDefault(mode="delta", max_chars=2000),
    "append_body": PreviewDefault(mode="delta", max_chars=2000),
    "append_chapter": PreviewDefault(mode="delta", max_chars=2000),
    "write_body": PreviewDefault(mode="tail", max_chars=4500),
    "rewrite_outline": PreviewDefault(mode="full", max_chars=4500),
}


def _preview_defaults(raw: Any) -> dict[str, PreviewDefault]:
    if not isinstance(raw, dict):
        return dict(_DEFAULT_PREVIEW)
    out: dict[str, PreviewDefault] = dict(_DEFAULT_PREVIEW)
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        out[str(key)] = PreviewDefault(
            mode=str(spec.get("mode", out.get(str(key), PreviewDefault()).mode)),
            max_chars=int(spec.get("max_chars", out.get(str(key), PreviewDefault()).max_chars)),
        )
    return out


def _cancel_map(raw: Any) -> dict[str, frozenset[str]]:
    defaults: dict[str, frozenset[str]] = {
        "reset_body": frozenset({"append_chapter", "append_body", "write_body"}),
        "rewrite_outline": frozenset({"append_chapter", "append_body"}),
        "edit_plot": frozenset(),
    }
    if not isinstance(raw, dict):
        return defaults
    out = dict(defaults)
    for action, kinds in raw.items():
        if isinstance(kinds, list):
            out[str(action)] = frozenset(str(k) for k in kinds)
    return out


def load_confirmation_gates_config() -> ConfirmationGatesConfig:
    raw = getattr(settings, "CONFIRMATION_GATES_CONFIG", None)
    if not isinstance(raw, dict):
        return ConfirmationGatesConfig(preview_defaults=dict(_DEFAULT_PREVIEW))

    def _frozenset(key: str, default: frozenset[str]) -> frozenset[str]:
        val = raw.get(key)
        if isinstance(val, list):
            return frozenset(str(x) for x in val)
        return default

    return ConfirmationGatesConfig(
        enabled=bool(raw.get("enabled", True)),
        material_intervention_actions=_frozenset(
            "material_intervention_actions",
            ConfirmationGatesConfig.material_intervention_actions,
        ),
        outcome_work_item_kinds=_frozenset(
            "outcome_work_item_kinds",
            ConfirmationGatesConfig.outcome_work_item_kinds,
        ),
        intervention_watch_actions=_frozenset(
            "intervention_watch_actions",
            ConfirmationGatesConfig.intervention_watch_actions,
        ),
        preview_defaults=_preview_defaults(raw.get("preview_defaults")),
        cancel_on_intervention=_cancel_map(raw.get("cancel_on_intervention")),
        excerpt_max_chars=int(raw.get("excerpt_max_chars", 4500)),
        delta_max_chars=int(raw.get("delta_max_chars", 2000)),
    )
