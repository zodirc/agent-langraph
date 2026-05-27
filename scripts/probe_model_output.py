#!/usr/bin/env python3
"""
Probe configured model API: record raw stream chunks, normalized text, and parseable fields.

Usage:
  python3 scripts/probe_model_output.py
  python3 scripts/probe_model_output.py --purpose reasoning --goal "规划阶段单次最多用多少 token？"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.prompts import build_planning_system_prompt, build_reasoning_system_prompt
from app.services.llm_client import _extract_json, _iter_json_objects, _normalize_content, get_llm, stream_structured
from app.services.llm_gateway import normalize_message_content
from app.services.reasoning_trace import extract_field_text, extract_plan_steps


_FIELD_PATTERNS = {
    "summary": re.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "thinking": re.compile(r'"thinking"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "content": re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "reasoning_content": re.compile(
        r'"reasoning_content"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL
    ),
    "text": re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "answer": re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
    "message": re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL),
}


def _chunk_repr(chunk: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": type(chunk).__name__}
    if hasattr(chunk, "content"):
        raw = getattr(chunk, "content", None)
        out["content_type"] = type(raw).__name__
        if isinstance(raw, str):
            out["content_preview"] = raw[:240]
        elif isinstance(raw, list):
            blocks = []
            for b in raw[:8]:
                if isinstance(b, dict):
                    blocks.append({k: (str(v)[:120] if k != "input" else v) for k, v in b.items()})
                else:
                    blocks.append({"repr": repr(b)[:200]})
            out["content_blocks"] = blocks
        else:
            out["content_repr"] = repr(raw)[:400]
    else:
        out["repr"] = repr(chunk)[:400]
    text, meta = normalize_message_content(getattr(chunk, "content", chunk))
    out["normalized_len"] = len(text)
    out["normalized_preview"] = text[:240]
    if meta.get("blocks"):
        out["block_types"] = [b.get("type") for b in meta["blocks"]]
    if meta.get("thinking_snippets"):
        out["thinking_snippets"] = meta["thinking_snippets"][:3]
    return out


def _analyze_accumulated(text: str) -> dict[str, Any]:
    objs = _iter_json_objects(text)
    keys_union: set[str] = set()
    for o in objs:
        keys_union.update(o.keys())
    fields: dict[str, Any] = {}
    for name, pat in _FIELD_PATTERNS.items():
        m = pat.search(text)
        if m:
            fields[name] = m.group(1)[:200].replace("\\n", "\n")
    return {
        "accumulated_chars": len(text),
        "json_object_count": len(objs),
        "json_keys_union": sorted(keys_union),
        "first_object_keys": sorted(objs[0].keys()) if objs else [],
        "regex_partial_fields": fields,
        "extract_summary": extract_field_text(text, "summary")[:200],
        "extract_plan_steps": extract_plan_steps(text)[:5],
    }


def probe_direct_http(purpose: str, system: str, user: str, *, max_events: int = 200) -> dict[str, Any]:
    """Raw Anthropic-style SSE against config base_url (full /v1/messages path)."""
    import httpx
    from app.config.settings import settings

    url = settings.MODEL_BASE_URL.rstrip("/")
    if not url.endswith("/v1/messages"):
        url = f"{url}/v1/messages"
    headers = {
        "x-api-key": settings.MODEL_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": settings.MODEL_NAME,
        "max_tokens": 1024,
        "stream": True,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    blocks: dict[int, dict[str, Any]] = {}
    event_counts: dict[str, int] = {}
    try:
        with httpx.stream("POST", url, headers=headers, json=body, timeout=120) as resp:
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "body": resp.read().decode()[:500]}
            n = 0
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                n += 1
                if n > max_events:
                    break
                obj = json.loads(data)
                et = str(obj.get("type") or "")
                event_counts[et] = event_counts.get(et, 0) + 1
                if et == "content_block_start":
                    idx = int(obj.get("index", 0))
                    block = obj.get("content_block") or {}
                    blocks[idx] = {"type": block.get("type"), "text": ""}
                elif et == "content_block_delta":
                    idx = int(obj.get("index", 0))
                    delta = obj.get("delta") or {}
                    if idx not in blocks:
                        blocks[idx] = {"type": "?", "text": ""}
                    if delta.get("type") == "thinking_delta":
                        blocks[idx]["text"] += str(delta.get("thinking") or "")
                    else:
                        blocks[idx]["text"] += str(delta.get("text") or "")
    except Exception as exc:
        return {"error": str(exc), "event_counts": event_counts, "blocks": blocks}

    analysis: dict[str, Any] = {}
    for idx, blk in sorted(blocks.items()):
        text = blk.get("text") or ""
        analysis[f"block_{idx}_{blk.get('type')}"] = {
            "chars": len(text),
            "head": text[:300],
            "tail": text[-200:] if len(text) > 200 else "",
            "json_keys": _analyze_accumulated(text).get("json_keys_union") if blk.get("type") == "text" else [],
            "extract_summary": _analyze_accumulated(text).get("extract_summary") if blk.get("type") == "text" else "",
        }
    return {
        "mode": "direct_http_sse",
        "url": url,
        "event_counts": event_counts,
        "blocks": analysis,
        "recommendation": {
            "user_answer_field": "text block JSON.summary",
            "thinking_field": "thinking block plain text (not JSON thinking key)",
        },
    }


def probe_stream(purpose: str, system: str, user: str, *, max_chunks: int = 80) -> dict[str, Any]:
    llm = get_llm(purpose)
    if llm is None:
        return {"error": "MODEL_ENABLED=false or no LLM"}

    from langchain_core.messages import HumanMessage, SystemMessage

    chunk_log: list[dict[str, Any]] = []
    accumulated = ""
    try:
        for i, chunk in enumerate(llm.stream([SystemMessage(content=system), HumanMessage(content=user)])):
            if i >= max_chunks:
                chunk_log.append({"note": f"truncated after {max_chunks} chunks"})
                break
            chunk_log.append(_chunk_repr(chunk))
            piece = _normalize_content(getattr(chunk, "content", ""))
            if piece:
                accumulated += piece
    except Exception as exc:
        return {"error": str(exc), "chunks": chunk_log, "partial_analysis": _analyze_accumulated(accumulated)}

    analysis = _analyze_accumulated(accumulated)
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    try:
        prefer = ("summary",) if purpose == "reasoning" else ("plan", "writing_intent", "mission")
        parsed = _extract_json(accumulated, prefer_keys=prefer)
    except Exception as exc:
        parse_error = str(exc)

    return {
        "mode": "native_stream",
        "chunk_count": len(chunk_log),
        "chunks_sample": chunk_log[:12],
        "chunks_tail": chunk_log[-3:] if len(chunk_log) > 12 else [],
        "accumulated_head": accumulated[:500],
        "accumulated_tail": accumulated[-500:] if len(accumulated) > 500 else "",
        "analysis": analysis,
        "parsed_json": parsed,
        "parse_error": parse_error,
    }


def probe_stream_structured(purpose: str, system: str, user: str) -> dict[str, Any]:
    accumulated = ""
    chunk_sizes: list[int] = []
    try:
        for piece in stream_structured(purpose, system, user):
            chunk_sizes.append(len(piece))
            accumulated += piece
    except Exception as exc:
        return {"error": str(exc), "partial": accumulated[:800]}

    analysis = _analyze_accumulated(accumulated)
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    try:
        prefer = ("summary",) if purpose == "reasoning" else ("plan", "writing_intent", "mission")
        parsed = _extract_json(accumulated, prefer_keys=prefer)
    except Exception as exc:
        parse_error = str(exc)

    return {
        "mode": "stream_structured (app path)",
        "yield_count": len(chunk_sizes),
        "yield_size_sample": chunk_sizes[:20],
        "analysis": analysis,
        "parsed_json": parsed,
        "parse_error": parse_error,
    }


def main() -> int:
    from app.config.settings import settings

    parser = argparse.ArgumentParser(description="Probe model API output shape")
    parser.add_argument("--purpose", choices=("reasoning", "planning"), default="reasoning")
    parser.add_argument("--goal", default="规划阶段单次最多使用多少 token？请给出具体数字。")
    parser.add_argument("--out", default=str(ROOT / "data/logs/model_probe.json"))
    args = parser.parse_args()

    if not settings.MODEL_ENABLED:
        print("MODEL_ENABLED=false — set ANTHROPIC_API_KEY and MODEL_ENABLED=true", file=sys.stderr)
        return 1
    if not str(settings.MODEL_API_KEY or "").strip():
        print("Missing MODEL_API_KEY / ANTHROPIC_API_KEY", file=sys.stderr)
        return 1

    purpose = args.purpose
    goal = args.goal
    if purpose == "reasoning":
        system = build_reasoning_system_prompt("direct")
        user = json.dumps(
            {
                "goal": goal,
                "turn_facts": {
                    "goal": goal,
                    "tools_executed": [
                        {
                            "tool": "get_runtime_info",
                            "output": {
                                "model_max_tokens_planning": settings.MODEL_MAX_TOKENS_PLANNING,
                                "model_max_tokens_reasoning": settings.MODEL_MAX_TOKENS_REASONING,
                            },
                        }
                    ],
                },
                "runtime_capabilities": {"model_name": settings.MODEL_NAME},
            },
            ensure_ascii=False,
        )
    else:
        system = build_planning_system_prompt()
        user = json.dumps(
            {"goal": goal, "risk_level": "LOW", "use_tools": True},
            ensure_ascii=False,
        )

    from app.services.llm_client import _normalize_anthropic_base_url

    report: dict[str, Any] = {
        "model": {
            "name": settings.MODEL_NAME,
            "base_url_config": settings.MODEL_BASE_URL,
            "base_url_langchain": _normalize_anthropic_base_url(settings.MODEL_BASE_URL),
            "provider": settings.MODEL_PROVIDER,
        },
        "purpose": purpose,
        "goal": goal,
        "direct_http_sse": probe_direct_http(purpose, system, user),
        "langchain_native_stream": probe_stream(purpose, system, user),
        "app_stream_structured": probe_stream_structured(purpose, system, user),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {out_path}")
    for label, block in (
        ("direct_http_sse", report["direct_http_sse"]),
        ("langchain_native_stream", report["langchain_native_stream"]),
        ("app_stream_structured", report["app_stream_structured"]),
    ):
        if block.get("error"):
            print(f"[{label}] ERROR: {block['error']}")
            continue
        if label == "direct_http_sse":
            print(f"[{label}] events={block.get('event_counts')}")
            for key, info in (block.get("blocks") or {}).items():
                print(f"  {key}: chars={info.get('chars')} summary={str(info.get('extract_summary') or '')[:80]}")
            continue
        a = block.get("analysis") or {}
        print(f"[{label}] chunks={block.get('chunk_count') or block.get('yield_count')}")
        print(f"  json_keys: {a.get('json_keys_union')}")
        print(f"  regex fields: {list((a.get('regex_partial_fields') or {}).keys())}")
        print(f"  extract_summary len={len(a.get('extract_summary') or '')}")
        pj = block.get("parsed_json") or {}
        if purpose == "reasoning":
            print(f"  parsed summary: {(pj.get('summary') or '')[:120]}")
        else:
            print(f"  parsed plan: {pj.get('plan')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
