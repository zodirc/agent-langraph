"""Load mode contracts from config (mode_contracts in config.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class ModeDelivery:
    primary: str = "reasoning_summary"
    secondary: str | None = None


@dataclass(frozen=True)
class ModeExecution:
    path: str = "reasoning"
    max_steps: int = 2
    max_write_actions: int = 0
    max_repair_attempts: int = 0


@dataclass(frozen=True)
class ModeVerify:
    enabled: bool = False
    backend_selector: str = "by_intent"


@dataclass(frozen=True)
class ModeGuards:
    forbid_routes: frozenset[str] = frozenset()
    force_isolate_from: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ModeSecurity:
    shell_access: bool = False
    network_access: bool = False
    path_scope: str = "session_root_only"


@dataclass(frozen=True)
class ModeContract:
    mode_id: str
    allowed_tools: frozenset[str] = frozenset()
    delivery: ModeDelivery = field(default_factory=ModeDelivery)
    execution: ModeExecution = field(default_factory=ModeExecution)
    verify: ModeVerify = field(default_factory=ModeVerify)
    guards: ModeGuards = field(default_factory=ModeGuards)
    security: ModeSecurity = field(default_factory=ModeSecurity)


def _parse_contract(mode_id: str, raw: dict[str, Any]) -> ModeContract:
    delivery_raw = raw.get("delivery") or {}
    exec_raw = raw.get("execution") or {}
    verify_raw = raw.get("verify") or {}
    guards_raw = raw.get("guards") or {}
    security_raw = raw.get("security") or {}
    tools_raw = raw.get("allowed_tools") or []
    forbid = guards_raw.get("forbid_routes") or []
    isolate_from = guards_raw.get("force_isolate_from") or []
    secondary = delivery_raw.get("secondary")
    return ModeContract(
        mode_id=mode_id,
        allowed_tools=frozenset(str(t) for t in tools_raw) if isinstance(tools_raw, list) else frozenset(),
        delivery=ModeDelivery(
            primary=str(delivery_raw.get("primary") or "reasoning_summary"),
            secondary=str(secondary).strip() if secondary else None,
        ),
        execution=ModeExecution(
            path=str(exec_raw.get("path") or "reasoning"),
            max_steps=int(exec_raw.get("max_steps", 2)),
            max_write_actions=int(exec_raw.get("max_write_actions", 0)),
            max_repair_attempts=int(exec_raw.get("max_repair_attempts", 0)),
        ),
        verify=ModeVerify(
            enabled=bool(verify_raw.get("enabled", False)),
            backend_selector=str(verify_raw.get("backend_selector") or "by_intent"),
        ),
        guards=ModeGuards(
            forbid_routes=frozenset(str(r) for r in forbid) if isinstance(forbid, list) else frozenset(),
            force_isolate_from=(
                frozenset(str(m) for m in isolate_from)
                if isinstance(isolate_from, list)
                else frozenset()
            ),
        ),
        security=ModeSecurity(
            shell_access=bool(security_raw.get("shell_access", False)),
            network_access=bool(security_raw.get("network_access", False)),
            path_scope=str(security_raw.get("path_scope") or "session_root_only"),
        ),
    )


def load_mode_contracts() -> dict[str, ModeContract]:
    raw = getattr(settings, "MODE_CONTRACTS_CONFIG", None)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, ModeContract] = {}
    for mode_id, spec in raw.items():
        if isinstance(spec, dict):
            out[str(mode_id)] = _parse_contract(str(mode_id), spec)
    return out


def get_mode_contract(mode_id: str) -> ModeContract | None:
    return load_mode_contracts().get(mode_id)


def contract_to_trace_dict(contract: ModeContract) -> dict[str, Any]:
    return {
        "mode_id": contract.mode_id,
        "allowed_tools": sorted(contract.allowed_tools),
        "delivery_primary": contract.delivery.primary,
        "delivery_secondary": contract.delivery.secondary,
        "execution_path": contract.execution.path,
        "max_steps": contract.execution.max_steps,
        "max_write_actions": contract.execution.max_write_actions,
        "max_repair_attempts": contract.execution.max_repair_attempts,
        "verify_enabled": contract.verify.enabled,
        "forbid_routes": sorted(contract.guards.forbid_routes),
        "shell_access": contract.security.shell_access,
        "network_access": contract.security.network_access,
        "path_scope": contract.security.path_scope,
    }
