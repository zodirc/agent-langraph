"""Build capability-aware FactBundle for OMAW workers."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.fact_bundle import FactBundle, FactSource, new_fact_bundle_id
from app.domain.worker_execution_policy import WorkerExecutionPolicy, policy_for_work_item
from app.runtime.state import AgentState, merge_state
from app.services.manuscript_context import (
    extract_chapter_text,
    extract_outline_chapter_brief,
    read_body_text,
    read_outline_text,
)
from app.services.manuscript_service import resolve_manuscript
from app.services.retrieval_policy import retrieval_domains_for_state
from app.services.writing_phases import load_story_bible


RAG_LAYER_TYPES = frozenset(
    {
        "project_rules",
        "story_bible",
        "outline",
        "chapter_facts",
        "external_reference",
    }
)


def _snapshot_version(state: AgentState) -> str:
    ms = state.get("manuscript") or {}
    return f"o{ms.get('outline_bytes', 0)}-b{ms.get('body_bytes', 0)}"


def _layer_query(agent: str, capability: str, chapter_index: int | None) -> str:
    ch = chapter_index or 0
    return f"writing mission {capability} chapter {ch} {agent}"


def _retrieve_by_rag_layers(
    state: AgentState,
    *,
    agent: str,
    capability: str,
    chapter_index: int | None,
    top_k: int,
) -> list[dict[str, Any]]:
    """Layer-tagged retrieval per ADR 6.3–6.4 (no full-manuscript vector recall)."""
    layers = list(getattr(settings, "RAG_WRITING_LAYERS", None) or RAG_LAYER_TYPES)
    if capability == "steer_replan" or agent == "planner":
        layers = ["project_rules", "outline", "chapter_facts"]
    elif capability == "polish_chapter" or agent == "editor":
        layers = ["project_rules", "story_bible", "chapter_facts", "outline"]
    elif capability == "consistency_check" or agent == "continuity":
        layers = ["story_bible", "chapter_facts", "external_reference"]
    elif capability == "write_outline":
        layers = ["project_rules", "outline", "external_reference"]
    elif agent == "writer" or capability in ("write_chapter", "append_body"):
        layers = ["project_rules", "story_bible", "outline", "chapter_facts", "external_reference"]

    combined: list[dict[str, Any]] = []
    for layer in layers:
        q = f"{layer} {_layer_query(agent, capability, chapter_index)}"
        hits = _hybrid_retrieve(state, query=q, domains=["writing", "common"], top_k=max(2, top_k // 3))
        for h in hits:
            h = dict(h)
            h["rag_layer"] = layer
            combined.append(h)
    return combined[:top_k]


def _editor_sources_from_verdict(
    state: AgentState,
    chapter_index: int | None,
) -> tuple[list[FactSource], list[str]]:
    """Editor must use review_verdict evidence sources (ADR 6.4.3)."""
    if not chapter_index:
        return [], []
    from app.domain.review_verdict import load_review_verdict

    verdict = load_review_verdict(state["task_id"], chapter_index)
    if not verdict:
        return [], []
    sources: list[FactSource] = []
    parts: list[str] = []
    for src in verdict.evidence.to_dict().items():
        sources.append(FactSource(type="review_verdict", ref=f"evidence/{src[0]}"))
    parts.append(f"[review_verdict ch{chapter_index}]\n{verdict.to_dict()}")
    return sources, parts


def _collect_layer_sources(
    state: AgentState,
    *,
    task_id: str,
    chapter_index: int | None,
    must_include: list[str],
    agent: str = "",
    capability: str = "",
) -> tuple[list[FactSource], list[str]]:
    sources: list[FactSource] = []
    evidence_parts: list[str] = []
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body_name = ms.body_path or "novel.txt"
    outline_name = ms.outline_path or "outline.txt"

    if "outline" in must_include or "outline" in RAG_LAYER_TYPES:
        outline_text = read_outline_text(task_id, outline_name, state=state)
        if chapter_index and outline_text:
            slice_text = extract_outline_chapter_brief(outline_text, chapter_index)
            if slice_text:
                sources.append(FactSource(type="outline", ref=f"outline/ch{chapter_index}"))
                evidence_parts.append(f"[outline ch{chapter_index}]\n{slice_text[:2000]}")
        elif outline_text:
            sources.append(FactSource(type="outline", ref="outline/full"))
            evidence_parts.append(f"[outline]\n{outline_text[:1500]}")

    if "story_bible" in must_include:
        bible = load_story_bible(task_id)
        if bible:
            sources.append(FactSource(type="story_bible", ref="bible/current"))
            excerpt = str(bible.get("summary") or "")[:800]
            threads = (bible.get("open_threads") or [])[:5]
            evidence_parts.append(
                f"[story_bible]\n{excerpt}\nthreads: {threads}"
            )

    if "chapter_summary" in must_include and chapter_index:
        body_text = read_body_text(task_id, body_name, state=state)
        if chapter_index > 1:
            prev = extract_chapter_text(body_text, chapter_index - 1)
            if prev:
                sources.append(
                    FactSource(type="chapter_summary", ref=f"summary/ch{chapter_index - 1}")
                )
                evidence_parts.append(f"[prev chapter]\n{prev[:1200]}")
        cur = extract_chapter_text(body_text, chapter_index)
        if cur:
            sources.append(
                FactSource(type="chapter_facts", ref=f"chapter_facts/ch{chapter_index}")
            )
            evidence_parts.append(f"[current chapter]\n{cur[:1200]}")

    if "user_constraints" in must_include or "project_rules" in must_include:
        payload = state.get("input_payload") or {}
        goal = str(payload.get("goal") or "").strip()
        intent = payload.get("intent_spec") or {}
        if goal:
            sources.append(FactSource(type="project_rules", ref="constraints/goal"))
            evidence_parts.append(f"[user_constraints]\n{goal[:1000]}")
        if intent:
            sources.append(FactSource(type="project_rules", ref="intent_spec"))
            evidence_parts.append(f"[intent_spec]\n{str(intent)[:800]}")

    if agent == "editor" or capability == "polish_chapter":
        ev_src, ev_parts = _editor_sources_from_verdict(state, chapter_index)
        sources.extend(ev_src)
        evidence_parts.extend(ev_parts)

    if agent == "planner" or capability == "steer_replan":
        payload = state.get("input_payload") or {}
        failures = payload.get("acceptance_replan") or payload.get("parallel_review_errors")
        if failures:
            sources.append(FactSource(type="chapter_facts", ref="planner/failures"))
            evidence_parts.append(f"[failure_summary]\n{str(failures)[:1200]}")

    return sources, evidence_parts


def _hybrid_retrieve(
    state: AgentState,
    *,
    query: str,
    domains: list[str],
    top_k: int,
) -> list[dict[str, Any]]:
    from app.services.knowledge_store import get_knowledge_store
    from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch

    domain_set = set(domains) or retrieval_domains_for_state(state)
    hits = get_knowledge_store().hybrid_search(query, domains=domain_set, top_k=top_k)
    return sanitize_retrieved_batch(hits)


def build_fact_bundle(
    state: AgentState,
    *,
    agent: str,
    capability: str,
    chapter_index: int | None = None,
    policy: Optional[WorkerExecutionPolicy] = None,
) -> dict[str, Any]:
    """
    Unified FactBundle construction — mandatory before worker capability execution.
    Returns dict suitable for payload['fact_bundle'] and audit.
    """
    from app.services.metrics_service import get_metrics_service

    task_id = str(state["task_id"])
    pol = policy or policy_for_work_item(capability)
    bundle_id = new_fact_bundle_id(
        task_id=task_id, chapter_index=chapter_index, capability=capability
    )
    sources, evidence_parts = _collect_layer_sources(
        state,
        task_id=task_id,
        chapter_index=chapter_index,
        must_include=pol.retrieval.must_include,
        agent=agent,
        capability=capability,
    )
    rag_domains = list(pol.retrieval.domains)
    top_k = int(getattr(settings, "MISSION_OMA_WORKER_RETRIEVAL_TOP_K", 8))
    query = _layer_query(agent, capability, chapter_index)
    knowledge_hits: list[dict[str, Any]] = []
    if getattr(settings, "MISSION_OMA_WORKER_RETRIEVAL_ENABLED", True):
        knowledge_hits = _retrieve_by_rag_layers(
            state,
            agent=agent,
            capability=capability,
            chapter_index=chapter_index,
            top_k=top_k,
        )
        for hit in knowledge_hits[:top_k]:
            doc_id = str(hit.get("doc_id") or hit.get("id") or "")
            chunk = str(hit.get("chunk_id") or hit.get("chunk_index") or "c0000")
            layer = str(hit.get("rag_layer") or "external_reference")
            if doc_id:
                sources.append(
                    FactSource(type=layer if layer in RAG_LAYER_TYPES else "knowledge_hit", ref=f"{doc_id}__{chunk}")
                )
                snippet = str(hit.get("content") or hit.get("text") or "")[:500]
                if snippet:
                    evidence_parts.append(f"[{layer} {doc_id}]\n{snippet}")

    evidence_text = "\n\n".join(evidence_parts)[:12000]
    bundle = FactBundle(
        fact_bundle_id=bundle_id,
        task_id=task_id,
        chapter_index=chapter_index,
        capability=capability,
        agent=agent,
        sources=sources,
        evidence_text=evidence_text,
        snapshot_version=_snapshot_version(state),
        rag_domains=rag_domains,
        rag_hit_count=len(knowledge_hits),
    )
    metrics = get_metrics_service()
    metrics.inc_fact_bundle_build(agent, capability)
    for src in sources:
        metrics.inc_fact_bundle_hit(src.type)

    return bundle.to_dict()


def attach_fact_bundle_to_state(
    state: AgentState,
    bundle: dict[str, Any],
) -> AgentState:
    payload = dict(state.get("input_payload") or {})
    payload["fact_bundle"] = bundle
    payload["fact_bundle_id"] = bundle.get("fact_bundle_id")
    rag_state = {
        "fact_bundle_id": bundle.get("fact_bundle_id"),
        "rag_domains": bundle.get("rag_domains"),
        "rag_hit_count": bundle.get("rag_hit_count"),
        "query": _layer_query(
            str(bundle.get("agent") or ""),
            str(bundle.get("capability") or ""),
            bundle.get("chapter_index"),
        ),
    }
    return merge_state(
        state,
        input_payload=payload,
        oma_rag_state=rag_state,
        retrieved_knowledge=state.get("retrieved_knowledge") or [],
    )
