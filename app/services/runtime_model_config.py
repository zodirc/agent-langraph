"""Runtime model configuration — hot-swap provider/model/key without process restart."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from app.llm.registry import (
    default_model_name,
    normalize_provider_id,
    resolve_model_base_url,
)


@dataclass(frozen=True)
class EffectiveModelConfig:
    provider: str
    model_name: str
    api_key: str
    base_url: str
    enabled: bool
    source: str  # "env" | "runtime"


def _mask_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"***{key[-4:]}"


def _env_defaults() -> dict[str, Any]:
    import app.config.settings as settings_module

    cfg = settings_module.settings
    return {
        "provider": cfg.MODEL_PROVIDER,
        "model_name": cfg.MODEL_NAME,
        "api_key": cfg.MODEL_API_KEY,
        "base_url": cfg.MODEL_BASE_URL,
        "enabled": cfg.MODEL_ENABLED,
    }


class RuntimeModelConfigStore:
    """In-memory override layered on startup Settings (from .env / YAML)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._override: dict[str, Any] | None = None

    def effective(self) -> EffectiveModelConfig:
        with self._lock:
            base = _env_defaults()
            if not self._override:
                return EffectiveModelConfig(
                    provider=base["provider"],
                    model_name=base["model_name"],
                    api_key=base["api_key"],
                    base_url=base["base_url"],
                    enabled=bool(base["enabled"]),
                    source="env",
                )

            merged = dict(base)
            for key in ("provider", "model_name", "base_url", "enabled"):
                if key in self._override and self._override[key] is not None:
                    merged[key] = self._override[key]

            api_key_override = self._override.get("api_key")
            if api_key_override is not None and str(api_key_override).strip():
                merged["api_key"] = str(api_key_override).strip()

            provider = normalize_provider_id(str(merged["provider"]))
            model_name = default_model_name(provider, str(merged["model_name"]))
            base_url = resolve_model_base_url(provider, str(merged["base_url"]))
            api_key = str(merged["api_key"] or "").strip()
            enabled_flag = bool(merged["enabled"]) and bool(api_key)

            return EffectiveModelConfig(
                provider=provider,
                model_name=model_name,
                api_key=api_key,
                base_url=base_url,
                enabled=enabled_flag,
                source="runtime",
            )

    def apply(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        enabled: bool | None = None,
    ) -> EffectiveModelConfig:
        with self._lock:
            if self._override is None:
                self._override = {}
            if provider is not None:
                self._override["provider"] = normalize_provider_id(provider)
            if model_name is not None:
                self._override["model_name"] = str(model_name).strip()
            if api_key is not None and str(api_key).strip():
                self._override["api_key"] = str(api_key).strip()
            if base_url is not None:
                self._override["base_url"] = str(base_url).strip()
            if enabled is not None:
                self._override["enabled"] = bool(enabled)

        self._invalidate_llm_cache()
        return self.effective()

    def reset(self) -> EffectiveModelConfig:
        with self._lock:
            self._override = None
        self._invalidate_llm_cache()
        return self.effective()

    def for_api(self) -> dict[str, Any]:
        eff = self.effective()
        env = _env_defaults()
        return {
            "provider": eff.provider,
            "model_name": eff.model_name,
            "base_url": eff.base_url,
            "enabled": eff.enabled,
            "source": eff.source,
            "api_key_configured": bool(eff.api_key),
            "api_key_hint": _mask_api_key(eff.api_key),
            "env_defaults": {
                "provider": env["provider"],
                "model_name": env["model_name"],
                "base_url": env["base_url"],
                "enabled": bool(env["enabled"]),
                "api_key_configured": bool(str(env["api_key"] or "").strip()),
                "api_key_hint": _mask_api_key(str(env["api_key"] or "")),
            },
            "providers": list_provider_options(),
        }

    @staticmethod
    def _invalidate_llm_cache() -> None:
        from app.services.llm_client import invalidate_llm_cache

        invalidate_llm_cache()


_store = RuntimeModelConfigStore()


def get_runtime_model_config() -> RuntimeModelConfigStore:
    return _store


def get_effective_model_config() -> EffectiveModelConfig:
    return _store.effective()


def list_provider_options() -> list[dict[str, Any]]:
    from app.llm.registry import list_provider_specs

    options: list[dict[str, Any]] = []
    for spec in list_provider_specs():
        options.append(
            {
                "id": spec.id,
                "label": spec.id,
                "api_family": spec.api_family,
                "default_base_url": spec.default_base_url,
                "default_model": spec.default_model,
            }
        )
    return options
