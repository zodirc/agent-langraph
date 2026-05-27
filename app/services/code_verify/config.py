"""Load code_artifact.verify from settings."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config.settings import settings


@dataclass(frozen=True)
class BackendConfig:
    id: str
    enabled: bool = True
    compile_cmd: tuple[str, ...] = ()
    run_cmd: tuple[str, ...] = ()
    source_extension: str = ".cpp"


@dataclass(frozen=True)
class VerifyConfig:
    enabled: bool = False
    on_failure: str = "repair"  # repair | mark_failed
    max_attempts: int = 2
    timeout_sec: int = 15
    workspace_root: str = "/tmp/agent-code-verify"
    backends: dict[str, BackendConfig] = field(default_factory=dict)
    language_to_backend: dict[str, str] = field(default_factory=dict)
    extension_to_backend: dict[str, str] = field(default_factory=dict)


def _cmd_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(x) for x in raw if str(x).strip())


def load_verify_config() -> VerifyConfig:
    raw = getattr(settings, "CODE_ARTIFACT_CONFIG", None)
    if not isinstance(raw, dict):
        return VerifyConfig()
    verify = raw.get("verify")
    if not isinstance(verify, dict):
        return VerifyConfig()

    backends_raw = raw.get("backends") or verify.get("backends") or {}
    backends: dict[str, BackendConfig] = {}
    if isinstance(backends_raw, dict):
        for bid, spec in backends_raw.items():
            if not isinstance(spec, dict):
                continue
            backends[str(bid)] = BackendConfig(
                id=str(bid),
                enabled=bool(spec.get("enabled", True)),
                compile_cmd=_cmd_list(spec.get("compile_cmd")),
                run_cmd=_cmd_list(spec.get("run_cmd")),
                source_extension=str(spec.get("source_extension") or ".txt"),
            )

    lang_map: dict[str, str] = {}
    ext_map: dict[str, str] = {}
    for key, value in (raw.get("default_backend_by_language") or {}).items():
        lang_map[str(key).lower()] = str(value)
    for key, value in (raw.get("default_backend_by_extension") or {}).items():
        ext_map[str(key).lower()] = str(value)

    if not backends:
        backends = {
            "cpp": BackendConfig(
                id="cpp",
                enabled=True,
                compile_cmd=("g++", "-std=c++17", "-Wall", "-Wextra", "-c", "{file}"),
                source_extension=".cpp",
            ),
            "python": BackendConfig(
                id="python",
                enabled=True,
                compile_cmd=("python3", "-m", "py_compile", "{file}"),
                source_extension=".py",
            ),
        }
    if not lang_map:
        lang_map = {"cpp": "cpp", "c++": "cpp", "python": "python", "py": "python"}
    if not ext_map:
        ext_map = {".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".py": "python"}

    return VerifyConfig(
        enabled=bool(verify.get("enabled", False)),
        on_failure=str(verify.get("on_failure", "repair")),
        max_attempts=int(verify.get("max_attempts", 2)),
        timeout_sec=int(verify.get("timeout_sec", 15)),
        workspace_root=str(verify.get("workspace_root", "/tmp/agent-code-verify")),
        backends=backends,
        language_to_backend=lang_map,
        extension_to_backend=ext_map,
    )


def resolve_backend_id(language: str, *, filename: str = "") -> str | None:
    cfg = load_verify_config()
    lang = (language or "").lower()
    if lang in cfg.language_to_backend:
        return cfg.language_to_backend[lang]
    if lang in ("c++", "cxx"):
        return cfg.language_to_backend.get("cpp")
    lower_name = filename.lower()
    for ext, bid in cfg.extension_to_backend.items():
        if lower_name.endswith(ext):
            return bid
    if lang in cfg.backends:
        return lang
    if lang == "cpp" and "cpp" in cfg.backends:
        return "cpp"
    if lang == "python" and "python" in cfg.backends:
        return "python"
    return None
