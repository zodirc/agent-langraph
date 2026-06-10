"""
Capability-based agent dispatch for Supervisor workers (Ch15 A2A adaptation).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.config.settings import settings
from app.domain.agent_message import AgentCard, AgentMessage
from app.domain.packs.registry import build_worker_catalog, get_domain_pack, list_pack_names
from app.services.llm_client import invoke_structured

logger = logging.getLogger(__name__)

_RUNTIME_AGENT_ID = "agent-langraph-runtime"


def runtime_agent_card() -> AgentCard:
    catalog = build_worker_catalog()
    caps: list[str] = []
    domains: list[str] = []
    for entry in catalog:
        domains.append(entry["domain"])
        for c in entry.get("capabilities") or [entry["domain"]]:
            if c not in caps:
                caps.append(c)
    return AgentCard(
        agent_id=_RUNTIME_AGENT_ID,
        name="Agent LangGraph Runtime",
        description="Multi-domain LangGraph agent runtime with supervisor workers",
        capabilities=caps,
        domains=domains,
        risk_level="LOW",
    )


def resolve_pack_for_capability(capability: str) -> Optional[str]:
    """Map capability string to domain pack name."""
    key = capability.lower().strip()
    if not key:
        return None
    for name in list_pack_names():
        pack = get_domain_pack(name)
        pack_caps = [str(c).lower() for c in (pack.metadata.get("capabilities") or [pack.name])]
        if key in pack_caps or key == pack.name:
            return pack.name
    return None


def _dispatch_external(message: AgentMessage, capability: str) -> AgentMessage | None:
    """Forward to external agent via HTTP when registry has a match."""
    if not settings.A2A_HTTP_FORWARD_ENABLED:
        return None
    from app.services.agent_registry import get_agent_registry

    target = (message.to_agent or "").strip()
    if target and target == _RUNTIME_AGENT_ID:
        return None
    registry = get_agent_registry()
    matches = registry.discover(capability)
    if not matches:
        return None
    card, base_url = matches[0]
    if target and card.agent_id != target:
        filtered = [(c, u) for c, u in matches if c.agent_id == target]
        if not filtered:
            return None
        card, base_url = filtered[0]
    try:
        import httpx

        resp = httpx.post(
            f"{base_url.rstrip('/')}/a2a/messages",
            json={"message": message.to_dict(), "parent_task_id": message.correlation_id},
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or data
        return AgentMessage.from_dict(result if isinstance(result, dict) else {"payload": result})
    except Exception as exc:
        logger.warning("A2A HTTP forward failed: %s", exc)
        return AgentMessage(
            message_id=message.message_id,
            from_agent=card.agent_id,
            to_agent=message.from_agent,
            capability=capability,
            payload={"error": str(exc)},
            correlation_id=message.correlation_id,
            message_type="error",
        )


def dispatch_message(message: AgentMessage, *, parent_task_id: str, user_id: str) -> AgentMessage:
    """Execute an AgentMessage and return a result envelope."""
    capability = message.capability.lower().strip()
    external = _dispatch_external(message, capability)
    if external is not None:
        return external
    domain = resolve_pack_for_capability(capability)
    if not domain:
        return AgentMessage(
            message_id=message.message_id,
            from_agent=message.to_agent or "worker",
            to_agent=message.from_agent,
            capability=capability,
            payload={"error": f"no worker for capability: {capability}"},
            correlation_id=message.correlation_id,
            message_type="error",
        )

    from app.domain.worker_executor import execute_domain_worker_with_retry

    goal = str(message.payload.get("goal") or message.payload.get("description") or "")
    outcome = execute_domain_worker_with_retry(
        parent_task_id=parent_task_id,
        user_id=user_id,
        domain=domain,
        description=goal,
        context=message.payload.get("context") if isinstance(message.payload.get("context"), dict) else {},
    )
    return AgentMessage(
        message_id=message.message_id,
        from_agent=domain,
        to_agent=message.from_agent,
        capability=capability,
        payload=outcome,
        correlation_id=message.correlation_id,
        message_type="result" if outcome.get("status") == "COMPLETED" else "error",
    )


def decompose_to_agent_messages(
    goal: str,
    domains: list[str] | None = None,
    *,
    parent_correlation_id: str = "",
) -> list[dict[str, Any]]:
    """
    Supervisor decomposition returning subtasks with required_capability + a2a_message.
    """
    allowed = [d.lower().strip() for d in (domains or list_pack_names()) if d]
    if not allowed:
        allowed = list_pack_names()
    catalog = build_worker_catalog(allowed)
    user_content = json.dumps(
        {"goal": goal, "worker_catalog": catalog},
        ensure_ascii=False,
    )
    system_prompt = (
        "You are a supervisor agent using capability-based routing (A2A).\n"
        f"Worker catalog: {json.dumps(catalog, ensure_ascii=False)}\n"
        'Return ONE JSON: {"subtasks":[{"required_capability":"<from catalog capabilities>",'
        '"description":"<subtask>"}]}\n'
        "Pick required_capability from the worker's capabilities list."
    )
    try:
        result = invoke_structured("planning", system_prompt, user_content)
        raw_tasks = result.get("subtasks", [])
        if isinstance(raw_tasks, list) and raw_tasks:
            normalized: list[dict[str, Any]] = []
            for item in raw_tasks:
                cap = str(item.get("required_capability", "")).lower().strip()
                domain = resolve_pack_for_capability(cap)
                if not domain:
                    continue
                desc = str(item.get("description", goal))
                msg = AgentMessage.task(
                    from_agent="supervisor",
                    to_agent=domain,
                    capability=cap or domain,
                    payload={"goal": desc, "context": {"parent_goal": goal}},
                    correlation_id=parent_correlation_id,
                )
                normalized.append(
                    {
                        "subtask_id": msg.message_id,
                        "domain": domain,
                        "required_capability": cap or domain,
                        "description": desc,
                        "status": "PENDING",
                        "a2a_message": msg.to_dict(),
                    }
                )
            if normalized:
                return normalized
    except Exception as exc:
        logger.warning("A2A decompose failed, using fallback: %s", exc)

    fallback_cap = "analysis" if "analysis" in allowed else allowed[0]
    domain = resolve_pack_for_capability(fallback_cap) or fallback_cap
    msg = AgentMessage.task(
        from_agent="supervisor",
        to_agent=domain,
        capability=fallback_cap,
        payload={"goal": goal},
        correlation_id=parent_correlation_id,
    )
    return [
        {
            "subtask_id": msg.message_id,
            "domain": domain,
            "required_capability": fallback_cap,
            "description": goal,
            "status": "PENDING",
            "a2a_message": msg.to_dict(),
        }
    ]


def run_subtasks_via_a2a(
    *,
    parent_task_id: str,
    user_id: str,
    subtasks: list[dict[str, Any]],
    context: dict[str, Any],
    max_workers: Optional[int] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Parallel capability dispatch using AgentMessage envelopes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.config.settings import settings

    workers = max_workers or settings.SUPERVISOR_MAX_WORKERS
    results: dict[str, Any] = {}
    errors: list[str] = []
    all_tool_results: list[dict[str, Any]] = []
    updated = [dict(item) for item in subtasks]

    def _run_one(st: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raw_msg = st.get("a2a_message")
        if isinstance(raw_msg, dict):
            message = AgentMessage.from_dict(raw_msg)
        else:
            message = AgentMessage.task(
                from_agent="supervisor",
                to_agent=st["domain"],
                capability=st.get("required_capability", st["domain"]),
                payload={"goal": st["description"], "context": context},
            )
        result_msg = dispatch_message(message, parent_task_id=parent_task_id, user_id=user_id)
        outcome = dict(result_msg.payload)
        outcome["domain"] = st["domain"]
        outcome["capability"] = st.get("required_capability")
        outcome["a2a"] = result_msg.to_dict()
        if result_msg.message_type == "result":
            outcome["status"] = outcome.get("status", "COMPLETED")
        else:
            outcome["status"] = "FAILED"
        return st["subtask_id"], outcome

    with ThreadPoolExecutor(max_workers=min(workers, max(len(updated), 1))) as pool:
        futures = {pool.submit(_run_one, st): st for st in updated}
        for future in as_completed(futures):
            st = futures[future]
            sid, outcome = future.result()
            results[sid] = outcome
            st["status"] = outcome.get("status", "FAILED")
            if outcome.get("tool_results"):
                all_tool_results.extend(outcome["tool_results"])
            if outcome.get("status") == "FAILED":
                errors.append(
                    f"capability {st.get('required_capability')}: {outcome.get('error', 'failed')}"
                )

    return results, updated, errors, all_tool_results
