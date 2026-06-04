"""Docker vs local config parity for mode routing blocks."""

from __future__ import annotations

import yaml
from pathlib import Path


def _load(name: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    with open(root / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_mode_routing_parity():
    local = _load("config.yaml")
    docker = _load("config.docker.yaml")
    assert local.get("mode_routing") == docker.get("mode_routing")


def test_mode_contracts_parity():
    local = _load("config.yaml")
    docker = _load("config.docker.yaml")
    assert local.get("mode_contracts") == docker.get("mode_contracts")


def test_project_verify_backends_parity():
    local = _load("config.yaml")
    docker = _load("config.docker.yaml")
    lv = (local.get("project_verify") or {}).get("backends")
    dv = (docker.get("project_verify") or {}).get("backends")
    assert lv == dv


def test_project_verify_limits_parity():
    local = _load("config.yaml")
    docker = _load("config.docker.yaml")
    for key in ("max_concurrent_verifies", "timeout_sec", "max_files"):
        assert (local.get("project_verify") or {}).get(key) == (docker.get("project_verify") or {}).get(
            key
        )
