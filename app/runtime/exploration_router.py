from __future__ import annotations

from app.runtime.state import AgentState


def route_after_explore_prune(state: AgentState) -> str:
    exploration = state.get("exploration") or {}
    round_no = int(exploration.get("round") or 0)
    max_rounds = int(exploration.get("max_rounds") or 2)
    if round_no < max_rounds and len(exploration.get("pruned") or []) > 0:
        return "hypothesize"
    return "finalize"
