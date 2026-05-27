"""
Exploration graph nodes (Ch21) — hypothesis → probe → score → prune (analysis pilot).
"""

from __future__ import annotations

import json
from app.config.prompts import agent_system_prompt
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.llm_client import invoke_structured
from app.services.state_store import get_state_store
from app.services.tool_registry import get_tool_registry

EXPLORE_HYPOTHESIZE_ROLE = """You are an exploration agent. Given a goal, return ONE JSON:
- "hypotheses": list of 2-4 objects {"id": "h1", "statement": "...", "probe_tool": "echo|calculator"}
- "success_metric": string describing how to judge outcomes
Use only probe_tool values from the allowed tools list."""


def explore_init_node(state: AgentState) -> AgentState:
    goal = str((state.get("input_payload") or {}).get("goal") or "")
    exploration = {
        "goal": goal,
        "domain": "analysis",
        "hypotheses": [],
        "scores": {},
        "active": [],
        "round": 0,
        "max_rounds": 2,
    }
    updated = merge_state(
        state,
        exploration=exploration,
        execution_mode="exploration",
        current_node="explore_init",
        status=TaskStatus.PLANNED.value,
        audit_log=append_audit(state, "explore_init", "success", {"goal": goal[:120]}),
    )
    get_state_store().save(updated)
    return updated


def explore_hypothesize_node(state: AgentState) -> AgentState:
    exploration = dict(state.get("exploration") or {})
    goal = exploration.get("goal") or ""
    tools = get_tool_registry().list_tools()
    allowed = [t for t in tools if t in ("echo", "calculator", "get_runtime_info")] or tools[:3]
    user_json = json.dumps({"goal": goal, "allowed_probe_tools": allowed}, ensure_ascii=False)
    system = agent_system_prompt(EXPLORE_HYPOTHESIZE_ROLE + f"\nAllowed tools: {allowed}")
    try:
        result = invoke_structured("planning", system, user_json)
        raw = result.get("hypotheses", [])
        hypotheses: list[dict] = []
        if isinstance(raw, list):
            for i, item in enumerate(raw[:4]):
                if not isinstance(item, dict):
                    continue
                hypotheses.append(
                    {
                        "id": str(item.get("id", f"h{i+1}")),
                        "statement": str(item.get("statement", "")),
                        "probe_tool": str(item.get("probe_tool", allowed[0] if allowed else "echo")),
                        "status": "pending",
                    }
                )
    except (ValueError, RuntimeError):
        hypotheses = [
            {
                "id": "h1",
                "statement": f"Probe goal directly: {goal[:80]}",
                "probe_tool": allowed[0] if allowed else "echo",
                "status": "pending",
            }
        ]
    if not hypotheses:
        hypotheses = [
            {
                "id": "h1",
                "statement": goal,
                "probe_tool": "echo",
                "status": "pending",
            }
        ]
    exploration["hypotheses"] = hypotheses
    exploration["active"] = [h["id"] for h in hypotheses]
    exploration["round"] = int(exploration.get("round", 0)) + 1
    updated = merge_state(
        state,
        exploration=exploration,
        current_node="explore_hypothesize",
        audit_log=append_audit(
            state,
            "explore_hypothesize",
            "success",
            {"count": len(hypotheses)},
        ),
    )
    get_state_store().save(updated)
    return updated


def explore_probe_node(state: AgentState) -> AgentState:
    registry = get_tool_registry()
    exploration = dict(state.get("exploration") or {})
    probes: list[dict] = []
    tool_results = list(state.get("tool_results") or [])
    for hyp in exploration.get("hypotheses") or []:
        if hyp.get("status") != "pending":
            continue
        tool_name = str(hyp.get("probe_tool", "echo"))
        params: dict = {}
        if tool_name == "echo":
            params = {"message": hyp.get("statement", "")[:200]}
        elif tool_name == "calculator":
            params = {"expression": "1+1"}
        try:
            result = registry.invoke(tool_name, params, user_role="user")
            probes.append(
                {
                    "hypothesis_id": hyp["id"],
                    "tool": tool_name,
                    "status": "ok",
                    "result": result,
                }
            )
            tool_results.append({"tool": tool_name, "status": "ok", "result": result})
            hyp["status"] = "probed"
            hyp["probe_output"] = result
        except Exception as exc:
            probes.append(
                {
                    "hypothesis_id": hyp["id"],
                    "tool": tool_name,
                    "status": "error",
                    "error": str(exc),
                }
            )
            hyp["status"] = "failed"
    exploration["probes"] = probes
    updated = merge_state(
        state,
        exploration=exploration,
        tool_results=tool_results,
        status=TaskStatus.TOOL_EXECUTED.value,
        current_node="explore_probe",
        audit_log=append_audit(state, "explore_probe", "success", {"probes": len(probes)}),
    )
    get_state_store().save(updated)
    return updated


def _rule_score(hypothesis: dict) -> float:
    if hypothesis.get("status") == "probed":
        return 0.75
    if hypothesis.get("status") == "failed":
        return 0.1
    return 0.0


def _llm_score_hypothesis(
    hypothesis: dict,
    probes: list[dict],
    success_metric: str,
) -> float:
    from app.config.settings import settings
    from app.services.llm_client import invoke_structured

    if not settings.EXPLORATION_LLM_SCORE_ENABLED or not settings.MODEL_ENABLED:
        return _rule_score(hypothesis)
    hid = hypothesis.get("id")
    probe_out = next((p for p in probes if p.get("hypothesis_id") == hid), {})
    system = (
        "Score hypothesis quality 0.0-1.0 after probe. "
        'Return JSON: {"score": 0.0-1.0, "rationale": "..."}'
    )
    try:
        result = invoke_structured(
            "routing",
            system,
            json.dumps(
                {
                    "hypothesis": hypothesis,
                    "probe": probe_out,
                    "success_metric": success_metric,
                },
                ensure_ascii=False,
            ),
        )
        return max(0.0, min(1.0, float(result.get("score", _rule_score(hypothesis)))))
    except Exception:
        return _rule_score(hypothesis)


def explore_score_node(state: AgentState) -> AgentState:
    exploration = dict(state.get("exploration") or {})
    scores: dict[str, float] = {}
    probes = list(exploration.get("probes") or [])
    success_metric = str(exploration.get("success_metric") or (state.get("input_payload") or {}).get("goal", ""))
    for hyp in exploration.get("hypotheses") or []:
        hid = hyp["id"]
        scores[hid] = _llm_score_hypothesis(hyp, probes, success_metric)
    exploration["scores"] = scores
    updated = merge_state(
        state,
        exploration=exploration,
        current_node="explore_score",
        audit_log=append_audit(state, "explore_score", "success", {"scores": scores}),
    )
    get_state_store().save(updated)
    return updated


def explore_prune_node(state: AgentState) -> AgentState:
    exploration = dict(state.get("exploration") or {})
    scores = exploration.get("scores") or {}
    threshold = 0.5
    kept = [h for h in exploration.get("hypotheses") or [] if scores.get(h["id"], 0) >= threshold]
    if not kept and exploration.get("hypotheses"):
        kept = [max(exploration["hypotheses"], key=lambda h: scores.get(h["id"], 0))]
    exploration["kept_hypotheses"] = kept
    exploration["pruned"] = [
        h["id"]
        for h in exploration.get("hypotheses") or []
        if h not in kept
    ]
    updated = merge_state(
        state,
        exploration=exploration,
        current_node="explore_prune",
        audit_log=append_audit(
            state,
            "explore_prune",
            "success",
            {"kept": len(kept), "pruned": len(exploration.get("pruned") or [])},
        ),
    )
    get_state_store().save(updated)
    return updated


def explore_finalize_node(state: AgentState) -> AgentState:
    exploration = dict(state.get("exploration") or {})
    kept = exploration.get("kept_hypotheses") or []
    lines = [f"- {h.get('statement', '')}" for h in kept]
    summary = "探索结论：\n" + ("\n".join(lines) if lines else "无假设通过筛选")
    reasoning_result = {
        "summary": summary,
        "confidence": 0.7 if kept else 0.4,
        "risk_level": "LOW",
        "structured": {
            "exploration": True,
            "hypotheses_kept": [h.get("id") for h in kept],
            "scores": exploration.get("scores"),
        },
    }
    updated = merge_state(
        state,
        reasoning_result=reasoning_result,
        status=TaskStatus.REASONED.value,
        current_node="explore_finalize",
        audit_log=append_audit(state, "explore_finalize", "success", {"kept": len(kept)}),
    )
    get_state_store().save(updated)
    return updated
