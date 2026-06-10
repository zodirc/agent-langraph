"""Config-driven delivery plan: how each inferred task kind is fulfilled."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState

WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})


def _route_audit_extensions() -> frozenset[str]:
    raw = getattr(settings, "ROUTE_AUDIT_CONFIG", None)
    if isinstance(raw, dict):
        ext_raw = raw.get("code_extensions")
        if isinstance(ext_raw, list):
            return frozenset(str(e).lower() for e in ext_raw)
    return frozenset({".cpp", ".cc", ".cxx", ".hpp", ".h", ".c", ".py", ".rs", ".go"})


def _manuscript_body_names() -> frozenset[str]:
    raw = getattr(settings, "ROUTE_AUDIT_CONFIG", None)
    if isinstance(raw, dict):
        body_raw = raw.get("manuscript_body_names")
        if isinstance(body_raw, list):
            return frozenset(str(n).lower() for n in body_raw)
    return frozenset({"novel.txt", "body.txt"})


def _is_code_filename(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _route_audit_extensions())


@dataclass(frozen=True)
class KindDelivery:
    primary: str = "reasoning_summary"
    secondary: str | None = None
    default_extension: str = ".cpp"
    preserve_whitespace: bool = True
    rewrite_manuscript_filenames: bool = True
    forbid_routes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DeliveryConfig:
    by_kind: dict[str, KindDelivery] = field(default_factory=dict)

    def kind_plan(self, kind: str) -> KindDelivery | None:
        return self.by_kind.get(kind)


def _parse_kind_delivery(raw: Any) -> KindDelivery | None:
    if not isinstance(raw, dict):
        return None
    forbid = raw.get("forbid_routes") or []
    return KindDelivery(
        primary=str(raw.get("primary") or "reasoning_summary"),
        secondary=str(raw["secondary"]).strip() if raw.get("secondary") else None,
        default_extension=str(raw.get("default_extension") or ".cpp"),
        preserve_whitespace=bool(raw.get("preserve_whitespace", True)),
        rewrite_manuscript_filenames=bool(raw.get("rewrite_manuscript_filenames", True)),
        forbid_routes=frozenset(str(r) for r in forbid) if isinstance(forbid, list) else frozenset(),
    )


def load_delivery_config() -> DeliveryConfig:
    raw = getattr(settings, "DELIVERY_CONFIG", None)
    if not isinstance(raw, dict):
        return DeliveryConfig(by_kind={})
    by_kind_raw = raw.get("by_kind") or {}
    by_kind: dict[str, KindDelivery] = {}
    if isinstance(by_kind_raw, dict):
        for kind_id, spec in by_kind_raw.items():
            parsed = _parse_kind_delivery(spec)
            if parsed:
                by_kind[str(kind_id)] = parsed
    return DeliveryConfig(by_kind=by_kind)


def resolve_delivery_plan(
    state: AgentState | dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Return delivery hints stored on payload (artifact profile, tool rewrites)."""
    delivery_cfg = load_delivery_config()
    kind = str(audit.get("inferred_kind") or "general")
    plan = delivery_cfg.kind_plan(kind)
    if not plan:
        return {
            "kind": kind,
            "primary": "reasoning_summary",
            "artifact_profile": audit.get("artifact_profile"),
        }
    return {
        "kind": kind,
        "primary": plan.primary,
        "secondary": plan.secondary,
        "preserve_whitespace": plan.preserve_whitespace,
        "rewrite_manuscript_filenames": plan.rewrite_manuscript_filenames,
        "default_extension": plan.default_extension,
        "artifact_profile": audit.get("artifact_profile") or (
            "source_code" if kind == "code" else None
        ),
    }


def default_code_filename(
    state: AgentState | dict[str, Any],
    *,
    extension: str = ".cpp",
) -> str:
    payload = state.get("input_payload") or {}
    tool_params = payload.get("tool_params") or {}
    for tool in WRITING_TOOL_NAMES:
        cfg = tool_params.get(tool)
        if isinstance(cfg, dict):
            name = str(cfg.get("filename") or "").strip()
            if name and _is_code_filename(name):
                return name
    task_id = str(state.get("task_id") or "snippet")
    slug = task_id.replace("-", "")[:8] or "main"
    ext = extension if extension.startswith(".") else f".{extension}"
    return f"{slug}{ext}"


def rewrite_manuscript_filenames_for_code(
    state: AgentState | dict[str, Any],
    *,
    extension: str = ".cpp",
) -> tuple[dict[str, Any], bool]:
    """Rewrite novel.txt-style tool targets to a code filename when kind is code."""
    body_names = _manuscript_body_names()
    payload = dict(state.get("input_payload") or {})
    tool_params = dict(payload.get("tool_params") or {})
    changed = False
    target_name = default_code_filename(state, extension=extension)

    for tool in WRITING_TOOL_NAMES:
        cfg = tool_params.get(tool)
        if not isinstance(cfg, dict):
            continue
        filename = str(cfg.get("filename") or "").strip()
        lower = filename.lower()
        if not filename or lower in body_names or not _is_code_filename(filename):
            tool_params[tool] = {**cfg, "filename": target_name}
            changed = True

    if changed:
        payload["tool_params"] = tool_params
    return payload, changed


def should_strip_writing_tools(
    planned_route: str,
    *,
    inferred_kind: str,
    delivery_cfg: DeliveryConfig | None = None,
) -> bool:
    """Keep write_text_artifact on tool_execution for code when route is tools-only."""
    delivery_cfg = delivery_cfg or load_delivery_config()
    plan = delivery_cfg.kind_plan(inferred_kind)
    if plan and plan.secondary == "tool_write" and planned_route == "writing_tools_only":
        return False
    return planned_route not in ("writing_tools_only",)
