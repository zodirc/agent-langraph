#!/usr/bin/env python3
"""Standalone open RAG benchmark runner (not part of regular pytest CI).

Usage:
  python tests/eval/run_open_rag_eval.py
  python tests/eval/run_open_rag_eval.py --benchmark qrecc
  python tests/eval/run_open_rag_eval.py --benchmark scifact
  python tests/eval/run_open_rag_eval.py \\
    --corpus tests/eval/open_rag/normalized/qrecc_test_corpus.jsonl \\
    --tasks tests/eval/open_rag/normalized/qrecc_test_rewrite_tasks.jsonl \\
    --top-k 5 \\
    --output tests/eval/reports/qrecc_eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OPEN_RAG_DIR = Path(__file__).parent / "open_rag" / "normalized"
REPORTS_DIR = Path(__file__).parent / "reports"

def _log(msg: str) -> None:
    print(f"[open-rag-eval] {msg}", flush=True)


BENCHMARKS: dict[str, dict[str, str]] = {
    "qrecc": {
        "corpus": "qrecc_sample_corpus.jsonl",
        "tasks": "qrecc_sample_tasks.jsonl",
        "report": "qrecc_eval.json",
    },
    "scifact": {
        "corpus": "scifact_sample_corpus.jsonl",
        "tasks": "scifact_sample_tasks.jsonl",
        "report": "scifact_eval.json",
    },
    "writing": {
        "corpus": "writing_corpus.jsonl",
        "tasks": "writing_tasks.jsonl",
        "report": "writing_eval.json",
    },
}

_WRITING_RAG_QUERY_SUFFIX = (
    "写作规范 口吻 去AI化 自然叙事 剧情连贯 伏笔 衔接 TXT排版 段落 章题 全角标点"
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parent_doc_id(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    parent = str(meta.get("parent_doc_id") or "").strip()
    if parent:
        return parent
    return str(hit.get("doc_id") or "")


def _normalize_hits_for_eval(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for hit in hits:
        doc_id = _parent_doc_id(hit)
        normalized.append({**hit, "doc_id": doc_id})
    return normalized


def _embedding_knowledge_yaml() -> str:
    """Reuse project embedding settings (config.yaml + env) for eval runs."""
    cfg_path = os.environ.get("CONFIG_PATH", "").strip() or str(ROOT / "config" / "config.yaml")
    try:
        from app.config.settings import Settings

        src = Settings(cfg_path)
        lines = [
            f"  embedding_model: {src.EMBEDDING_MODEL}",
            f"  embedding_base_url: {src.EMBEDDING_BASE_URL}",
            f"  embedding_api_model: {src.EMBEDDING_API_MODEL}",
            f"  embedding_api_key: {src.EMBEDDING_API_KEY}",
            f"  embedding_timeout_sec: {src.EMBEDDING_TIMEOUT_SEC}",
            f"  local_embedding_model_name: {src.LOCAL_EMBEDDING_MODEL_NAME}",
            f"  local_embedding_device: {src.LOCAL_EMBEDDING_DEVICE}",
        ]
        if src.LOCAL_EMBEDDING_CACHE_DIR:
            lines.append(f"  local_embedding_cache_dir: {src.LOCAL_EMBEDDING_CACHE_DIR}")
        return "\n".join(lines) + "\n"
    except Exception:
        return (
            "  embedding_model: local_minilm\n"
            "  local_embedding_model_name: sentence-transformers/all-MiniLM-L6-v2\n"
            "  local_embedding_device: cpu\n"
        )


def _bootstrap_knowledge_store(tmp_dir: Path):
    from app.config.settings import Settings
    from app.services.knowledge_store import KnowledgeStore, get_knowledge_store

    db_path = tmp_dir / "open_rag_eval.db"
    vector_path = tmp_dir / "vectorstore"
    embedding_block = _embedding_knowledge_yaml()
    config_content = f"""
model:
  provider: anthropic
  name: claude-sonnet-4-5
  api_key: ""
  enabled: false
storage:
  sqlite_path: {db_path}
  vectorstore_path: {vector_path}
knowledge:
  backend: chroma
  top_k: 5
  similarity_threshold: 0.1
{embedding_block}app:
  env: test
  secret_key: open-rag-eval
auth:
  enabled: false
"""
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(config_content, encoding="utf-8")

    import app.config.settings as settings_module
    import app.services.knowledge_store as knowledge_store_module

    settings = Settings(str(config_path))
    settings_module.settings = settings
    knowledge_store_module.settings = settings
    store = KnowledgeStore(settings.SQLITE_PATH)
    knowledge_store_module.get_knowledge_store = lambda: store
    return store


def _writing_query_from_task(task: dict[str, Any]) -> str:
    base = str(task.get("query") or "").strip()
    meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    if not meta.get("enrich_writing"):
        return base
    if "去AI化" in base and "TXT排版" in base:
        return base[:2000]
    return f"{base} {_WRITING_RAG_QUERY_SUFFIX}".strip()[:2000]


def import_corpus(
    store,
    corpus_rows: list[dict[str, Any]],
    *,
    domain: str = "common",
    progress_every: int = 50,
) -> int:
    imported = 0
    total = len(corpus_rows)
    for idx, row in enumerate(corpus_rows, start=1):
        doc_id = str(row.get("doc_id") or "").strip()
        title = str(row.get("title") or doc_id or "Untitled").strip()
        content = str(row.get("content") or "").strip()
        if not doc_id or not content:
            continue
        row_meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        doc_domain = str(row_meta.get("domain") or domain)
        store.upsert_document(
            title,
            content,
            metadata={"domain": doc_domain, "source": "open_rag"},
            doc_id=doc_id,
        )
        imported += 1
        if progress_every > 0 and (idx % progress_every == 0 or idx == total):
            _log(f"corpus import {idx}/{total} (upserted={imported})")
    return imported


def run_eval(
    *,
    benchmark: str,
    corpus_path: Path,
    tasks_path: Path,
    top_k: int,
    output_path: Path | None,
) -> dict[str, Any]:
    from app.services.rag_domain_eval import evaluate_domain_tasks

    corpus_rows = _load_jsonl(corpus_path)
    tasks = _load_jsonl(tasks_path)
    if not corpus_rows:
        raise ValueError(f"No corpus rows found in {corpus_path}")
    if not tasks:
        raise ValueError(f"No tasks found in {tasks_path}")

    with tempfile.TemporaryDirectory(prefix="open_rag_eval_") as tmp:
        _log("[1/4] bootstrap knowledge store + load embedding model")
        store = _bootstrap_knowledge_store(Path(tmp))
        _log(f"[2/4] import corpus ({len(corpus_rows)} docs, embedding per doc)")
        imported = import_corpus(store, corpus_rows)
        _log(f"      done: imported={imported}")

        def _search(query: str, domain: str, k: int) -> list[dict[str, Any]]:
            hits = store.hybrid_search(query, top_k=k, domains={domain})
            return _normalize_hits_for_eval(hits)

        query_fn = _writing_query_from_task if benchmark == "writing" else None
        _log(f"[3/4] hybrid_search on {len(tasks)} queries (top_k={top_k})")
        result = evaluate_domain_tasks(
            tasks,
            top_k=top_k,
            search_fn=_search,
            query_fn=query_fn,
            progress_every=50,
            progress_prefix="[open-rag-eval]",
        )
        _log("[4/4] aggregate metrics and write report")
        report = {
            "benchmark": benchmark,
            "corpus_path": str(corpus_path),
            "tasks_path": str(tasks_path),
            "top_k": top_k,
            "imported_docs": imported,
            "task_count": len(tasks),
            **result,
        }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run open RAG benchmark against normalized corpus/tasks.")
    parser.add_argument(
        "--benchmark",
        choices=sorted(BENCHMARKS),
        default="qrecc",
        help="Preset sample benchmark (default: qrecc)",
    )
    parser.add_argument("--corpus", type=Path, default=None, help="Path to normalized corpus.jsonl")
    parser.add_argument("--tasks", type=Path, default=None, help="Path to normalized tasks.jsonl")
    parser.add_argument("--sample", action="store_true", help="Use bundled sample files for --benchmark")
    parser.add_argument("--top-k", type=int, default=5, help="Retrieval top-k (default: 5)")
    parser.add_argument("--output", type=Path, default=None, help="Report output path")
    args = parser.parse_args()

    preset = BENCHMARKS[args.benchmark]
    use_sample = args.sample or (args.corpus is None and args.tasks is None)
    if use_sample:
        corpus_path = OPEN_RAG_DIR / preset["corpus"]
        tasks_path = OPEN_RAG_DIR / preset["tasks"]
        output_path = args.output or (REPORTS_DIR / preset["report"])
    else:
        if args.corpus is None or args.tasks is None:
            print("Provide both --corpus and --tasks, or omit them to use --benchmark sample.", file=sys.stderr)
            return 1
        corpus_path = args.corpus
        tasks_path = args.tasks
        output_path = args.output or (REPORTS_DIR / preset["report"])

    if not corpus_path.exists():
        print(f"Corpus not found: {corpus_path}", file=sys.stderr)
        if args.benchmark == "qrecc":
            print("Run convert_qrecc.py first, or use the bundled qrecc sample.", file=sys.stderr)
        else:
            print("Run convert_beir_scifact.py first, or use the bundled scifact sample.", file=sys.stderr)
        return 1
    if not tasks_path.exists():
        print(f"Tasks not found: {tasks_path}", file=sys.stderr)
        return 1

    os.environ.setdefault("ANTHROPIC_API_KEY", "")
    report = run_eval(
        benchmark=args.benchmark,
        corpus_path=corpus_path,
        tasks_path=tasks_path,
        top_k=args.top_k,
        output_path=output_path,
    )
    summary = report.get("summary") or {}
    print(json.dumps({"benchmark": args.benchmark, "summary": summary, "output": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
