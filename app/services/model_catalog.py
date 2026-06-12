"""Configurable model catalog — context window sizes for UI metering (Cursor/Copilot-style)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    label: str
    provider: str
    model_name: str
    context_window_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "model_name": self.model_name,
            "context_window_tokens": self.context_window_tokens,
        }


def _infer_context_window(model_name: str) -> int:
    """Best-effort context window when not configured."""
    name = (model_name or "").lower()
    if "opus" in name or "sonnet-4" in name or "claude-4" in name:
        return 200_000
    if "gpt-4" in name or "gpt-5" in name or "o1" in name or "o3" in name:
        return 128_000
    if "deepseek" in name:
        return 64_000
    if "glm" in name:
        return 128_000
    return int(getattr(settings, "MODEL_CONTEXT_WINDOW", 128_000) or 128_000)


def _parse_catalog_raw(raw: Any) -> list[ModelCatalogEntry]:
    if not isinstance(raw, list):
        return []
    entries: list[ModelCatalogEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        model_name = str(item.get("model_name") or item.get("name") or "").strip()
        if not model_name:
            continue
        entry_id = str(item.get("id") or model_name).strip()
        label = str(item.get("label") or model_name).strip()
        provider = str(item.get("provider") or settings.MODEL_PROVIDER).strip()
        window = int(item.get("context_window_tokens") or item.get("context_window") or 0)
        if window <= 0:
            window = _infer_context_window(model_name)
        entries.append(
            ModelCatalogEntry(
                id=entry_id,
                label=label,
                provider=provider,
                model_name=model_name,
                context_window_tokens=window,
            )
        )
    return entries


def get_model_catalog() -> list[ModelCatalogEntry]:
    """Return configured catalog; always includes the active deployment model."""
    configured = _parse_catalog_raw(getattr(settings, "MODEL_CATALOG", None))
    by_name = {e.model_name: e for e in configured}
    from app.services.runtime_model_config import get_effective_model_config

    eff = get_effective_model_config()
    active_name = str(eff.model_name or "").strip()
    if active_name and active_name not in by_name:
        configured.insert(
            0,
            ModelCatalogEntry(
                id=active_name,
                label=active_name,
                provider=str(eff.provider),
                model_name=active_name,
                context_window_tokens=int(
                    getattr(settings, "MODEL_CONTEXT_WINDOW", 0)
                    or _infer_context_window(active_name)
                ),
            ),
        )
    elif not configured and active_name:
        configured = [
            ModelCatalogEntry(
                id=active_name,
                label=active_name,
                provider=str(eff.provider),
                model_name=active_name,
                context_window_tokens=int(
                    getattr(settings, "MODEL_CONTEXT_WINDOW", 0)
                    or _infer_context_window(active_name)
                ),
            )
        ]
    return configured


def resolve_catalog_entry(
    *,
    model_id: str | None = None,
    model_name: str | None = None,
) -> ModelCatalogEntry | None:
    catalog = get_model_catalog()
    if model_id:
        for entry in catalog:
            if entry.id == model_id or entry.model_name == model_id:
                return entry
    if model_name:
        for entry in catalog:
            if entry.model_name == model_name:
                return entry
    return catalog[0] if catalog else None


def resolve_context_window_tokens(
    *,
    model_id: str | None = None,
    model_name: str | None = None,
) -> int:
    entry = resolve_catalog_entry(model_id=model_id, model_name=model_name)
    if entry:
        return entry.context_window_tokens
    name = model_name or str(settings.MODEL_NAME or "")
    return int(getattr(settings, "MODEL_CONTEXT_WINDOW", 0) or _infer_context_window(name))


def catalog_for_api() -> list[dict[str, Any]]:
    return [e.to_dict() for e in get_model_catalog()]
