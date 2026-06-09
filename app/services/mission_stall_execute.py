"""Deterministic read→edit when mission stalls (optimization.md §3.2)."""

from __future__ import annotations

from app.runtime.state import AgentState, merge_state


def stall_deterministic_already_tried(payload: dict, signature: str) -> bool:
    tried = payload.get("stall_deterministic_tried_signatures") or []
    if not isinstance(tried, list):
        return False
    return str(signature) in {str(x) for x in tried}


def mark_stall_deterministic_tried(payload: dict, signature: str) -> dict:
    out = dict(payload)
    tried = [str(x) for x in (out.get("stall_deterministic_tried_signatures") or []) if str(x).strip()]
    sig = str(signature)
    if sig not in tried:
        tried.append(sig)
    out["stall_deterministic_tried_signatures"] = tried[-8:]
    return out


def apply_stall_deterministic_payload(payload: dict, *, signature: str) -> dict:
    out = dict(payload)
    out["stall_force_deterministic_edit"] = True
    out["skip_planning_llm"] = True
    out["force_slow_reasoning"] = False
    out = mark_stall_deterministic_tried(out, signature)
    return out


def run_stall_deterministic_edit(state: AgentState) -> AgentState:
    """Bypass planning — run outline read→edit (or rewrite) directly."""
    from app.services.artifact_resolver import outline_exists, resolve_artifact_target
    from app.services.edit_scope import classify_edit_action
    from app.services.outline_steer_patch import run_outline_edit_via_tools

    payload = dict(state.get("input_payload") or {})
    steer = str(payload.get("latest_steer_message") or payload.get("goal") or "")
    if not outline_exists(state):
        from app.nodes.writing_node import writing_node

        payload["writing_intent"] = {
            "enabled": True,
            "action": "rewrite_outline",
            "reason": steer[:240] or "stall deterministic rewrite",
            "source": "stall_deterministic",
        }
        return writing_node(merge_state(state, input_payload=payload))

    target = resolve_artifact_target(
        state,
        action="edit_plot",
        target_hint="outline",
        require_exists=True,
    )
    action = classify_edit_action(steer, outline_exists=True)
    if action == "rewrite_outline":
        from app.nodes.writing_node import writing_node

        payload["writing_intent"] = {
            "enabled": True,
            "action": "rewrite_outline",
            "reason": steer[:240] or "stall deterministic rewrite",
            "source": "stall_deterministic",
        }
        return writing_node(merge_state(state, input_payload=payload))

    out = run_outline_edit_via_tools(
        state,
        spec={
            "filename": target.filename,
            "steer_correction": steer,
            "source": "stall_deterministic",
        },
    )
    payload = dict(out.get("input_payload") or payload)
    payload.pop("stall_force_deterministic_edit", None)
    return merge_state(out, input_payload=payload)
