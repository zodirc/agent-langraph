"""Tests for context fingerprint dedup and memory hit filtering."""

from app.services.context_fingerprint import (
    dedupe_context_items,
    is_near_duplicate,
    normalize_content_text,
    registry_fingerprints,
    strict_dedupe_key,
)
from app.services.context_items import ContextItem, new_context_id
from app.services.context_registry import merge_registry_items
from app.services.code_artifact_pipeline import filter_memory_hits


def test_strict_dedupe_key_uses_memory_id():
    item = ContextItem(
        id=new_context_id("mem"),
        kind="episodic_memory",
        source="memory",
        content="Episode result: build succeeded with minor warnings.",
        meta={"memory_id": "ep_123"},
    )
    variant = ContextItem(
        id=new_context_id("mem"),
        kind="episodic_memory",
        source="memory",
        content="Build succeeded with minor warnings (episode result).",
        meta={"memory_id": "ep_123"},
    )
    assert strict_dedupe_key(item) == strict_dedupe_key(variant)


def test_dedupe_context_items_collapses_near_duplicates():
    a = ContextItem(
        id=new_context_id(),
        kind="knowledge",
        source="retrieval",
        content="The API returns 404 when the resource id is missing from storage.",
    )
    b = ContextItem(
        id=new_context_id(),
        kind="knowledge",
        source="retrieval",
        content="API returns 404 when resource id is missing from storage.",
    )
    out = dedupe_context_items([a, b])
    assert len(out) == 1


def test_normalize_content_text_strips_dynamic_ids():
    raw = "ctx_abcd1234ef56 found compile error in main.cpp"
    normalized = normalize_content_text(raw)
    assert "ctx_" not in normalized
    assert "compile error" in normalized


def test_merge_registry_items_uses_fingerprint():
    state = {"task_id": "t1", "session_id": "s1", "context_item_registry": []}
    item = ContextItem(
        id=new_context_id("rk"),
        kind="knowledge",
        source="retrieval",
        content="doc body",
        meta={"doc_id": "doc-1"},
    )
    updated = merge_registry_items(state, [item])
    dup = ContextItem(
        id=new_context_id("rk"),
        kind="knowledge",
        source="retrieval",
        content="rewritten doc body with same id",
        meta={"doc_id": "doc-1"},
    )
    updated2 = merge_registry_items(updated, [dup])
    assert len(updated2["context_item_registry"]) == 1


def test_registry_fingerprints_reads_existing_registry():
    state = {
        "context_item_registry": [
            {
                "id": "rt_abc",
                "kind": "tool_output",
                "content": "[grep] ok: match",
                "meta": {"tool": "grep", "status": "ok"},
            }
        ]
    }
    fps = registry_fingerprints(state)
    assert len(fps) == 1


def test_filter_memory_hits_dedupes_against_working_memory():
    state = {
        "task_type": "coding",
        "session_turn": 5,
        "input_payload": {"goal": "fix compile error in main.cpp"},
        "plan": ["inspect error", "patch file"],
        "turn_facts": {
            "executed_actions": ["read main.cpp"],
            "tools_executed": [{"tool": "read_file", "status": "ok"}],
        },
        "conversation_history": [
            {"role": "user", "content": "fix compile error in main.cpp"},
        ],
    }
    hits = [
        {"id": "m1", "content": "User asked to fix compile error in main.cpp", "score": 0.9},
        {"id": "m2", "content": "Previous project used CMake for C++ builds", "score": 0.5},
    ]
    filtered = filter_memory_hits(state, hits)
    assert len(filtered) == 1
    assert filtered[0]["id"] == "m2"


def test_is_near_duplicate_detects_substring_facts():
    assert is_near_duplicate(
        "User asked to fix compile error in main.cpp",
        "fix compile error in main.cpp",
    )
