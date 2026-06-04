"""Load project_verify configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class ProjectBackendConfig:
    id: str
    enabled: bool = True
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required_files: frozenset[str] = frozenset()
    entry_js_names: frozenset[str] = frozenset({"game.js", "main.js", "app.js"})
    allowed_targets: frozenset[str] = frozenset({"demo"})


@dataclass(frozen=True)
class ProjectVerifyConfig:
    enabled: bool = True
    timeout_sec: int = 30
    max_concurrent_verifies: int = 2
    max_files: int = 64
    max_file_bytes: int = 524288
    workspace_root: str = "./data/project_verify"
    backends: dict[str, ProjectBackendConfig] = field(default_factory=dict)
    backend_by_intent: dict[str, str] = field(default_factory=dict)
    backend_by_language: dict[str, str] = field(default_factory=dict)


def _cmd_tuple(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return ()


def load_project_verify_config() -> ProjectVerifyConfig:
    raw = getattr(settings, "PROJECT_VERIFY_CONFIG", None)
    if not isinstance(raw, dict):
        return ProjectVerifyConfig()

    backends_raw = raw.get("backends") or {}
    backends: dict[str, ProjectBackendConfig] = {}
    if isinstance(backends_raw, dict):
        for bid, spec in backends_raw.items():
            if not isinstance(spec, dict):
                continue
            cmds_raw = spec.get("commands") or {}
            commands: dict[str, tuple[str, ...]] = {}
            if isinstance(cmds_raw, dict):
                for name, cmd in cmds_raw.items():
                    commands[str(name)] = _cmd_tuple(cmd)
            req = spec.get("required_files") or []
            entry = spec.get("entry_js_names") or ["game.js", "main.js", "app.js"]
            targets = spec.get("allowed_targets") or ["demo"]
            backends[str(bid)] = ProjectBackendConfig(
                id=str(bid),
                enabled=bool(spec.get("enabled", True)),
                commands=commands,
                required_files=frozenset(str(f) for f in req) if isinstance(req, list) else frozenset(),
                entry_js_names=frozenset(str(n) for n in entry) if isinstance(entry, list) else frozenset(),
                allowed_targets=frozenset(str(t) for t in targets) if isinstance(targets, list) else frozenset({"demo"}),
            )

    by_intent = raw.get("backend_by_intent") or {}
    by_lang = raw.get("backend_by_language") or {}
    return ProjectVerifyConfig(
        enabled=bool(raw.get("enabled", True)),
        timeout_sec=int(raw.get("timeout_sec", 30)),
        max_concurrent_verifies=max(1, int(raw.get("max_concurrent_verifies", 2))),
        max_files=int(raw.get("max_files", 64)),
        max_file_bytes=int(raw.get("max_file_bytes", 524288)),
        workspace_root=str(raw.get("workspace_root") or "./data/project_verify"),
        backends=backends,
        backend_by_intent=(
            {str(k): str(v) for k, v in by_intent.items()} if isinstance(by_intent, dict) else {}
        ),
        backend_by_language=(
            {str(k): str(v) for k, v in by_lang.items()} if isinstance(by_lang, dict) else {}
        ),
    )


def allowed_backend_ids() -> frozenset[str]:
    cfg = load_project_verify_config()
    ids = set(cfg.backends.keys())
    ids.update(cfg.backend_by_language.values())
    ids.update(cfg.backend_by_intent.values())
    ids.update({"cpp", "python"})
    return frozenset(ids)
