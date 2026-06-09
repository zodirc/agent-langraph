"""Fast structural smoke test for the unified-core refactor.

Verifies the repo is importable and the agent graph compiles, without needing an
LLM key or network. Run: python scripts/smoke_imports.py
"""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULES = [
    "app.domain.action",
    "app.runtime.graph",
    "app.runtime.router",
    "app.runtime.runtime_router",
    "app.runtime.planning_gate_router",
    "app.runtime.supervisor_graph",
    "app.runtime.worker_graph",
    "app.nodes.planning_node",
    "app.nodes.tool_node",
    "app.nodes.reasoning_node",
    "app.nodes.reasoning_or_writing_node",
    "app.services.graph_runner",
    "app.services.session_controller",
    "app.services.turn_contract",
    "app.services.artifact_tools",
    "app.api.task_api",
]


def main() -> int:
    failed = []
    for mod in MODULES:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            failed.append((mod, repr(exc)))
    if failed:
        print("IMPORT FAILURES:")
        for mod, err in failed:
            print(f"  {mod}: {err}")
        return 1
    from app.runtime.graph import build_agent_graph

    build_agent_graph()
    print("SMOKE OK: all imports + graph build succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
