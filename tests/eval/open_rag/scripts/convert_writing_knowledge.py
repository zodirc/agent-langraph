#!/usr/bin/env python3
"""Build writing-domain normalized corpus + tasks from repo knowledge markdown.

Usage:
  python tests/eval/open_rag/scripts/convert_writing_knowledge.py
  python tests/eval/open_rag/scripts/convert_writing_knowledge.py --include-common-decoys
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

OPEN_RAG_DIR = Path(__file__).resolve().parents[1]
NORMALIZED_DIR = OPEN_RAG_DIR / "normalized"
TASKS_SPEC = OPEN_RAG_DIR / "writing_tasks.yaml"

# Align with app.services.knowledge_seed.DEFAULT_DOCUMENTS
_CORPUS_SPECS: list[dict[str, Any]] = [
    {
        "doc_id": "builtin-writing-guidelines",
        "title": "长文写作规范",
        "path_key": "writing_guidelines",
        "domain": "writing",
    },
    {
        "doc_id": "builtin-prose-voice-format",
        "title": "写作口吻与TXT排版规范",
        "path_key": "prose_voice",
        "domain": "writing",
    },
]

_DECOY_SPECS: list[dict[str, Any]] = [
    {
        "doc_id": "builtin-architecture",
        "title": "LangGraph Agent Runtime Overview",
        "content": (
            "This project uses LangGraph as the execution runtime with nodes for planning, "
            "retrieval, tool execution, reasoning, policy checks, human review, output, "
            "and memory writeback."
        ),
        "domain": "common",
    },
    {
        "doc_id": "builtin-retrieval",
        "title": "Knowledge Retrieval Strategy",
        "content": (
            "Knowledge retrieval uses hybrid search combining ChromaDB vector similarity "
            "and SQLite keyword matching merged with reciprocal rank fusion."
        ),
        "domain": "common",
    },
]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _resolve_path(key: str) -> Path:
    from app.services.knowledge_paths import code_editing_guidelines_path, prose_voice_format_path, writing_guidelines_path

    mapping = {
        "writing_guidelines": writing_guidelines_path,
        "prose_voice": prose_voice_format_path,
        "code_editing": code_editing_guidelines_path,
    }
    return mapping[key]()


def convert_writing(*, include_common_decoys: bool = True) -> tuple[Path, Path]:
    corpus_rows: list[dict] = []
    for spec in _CORPUS_SPECS:
        path = _resolve_path(str(spec["path_key"]))
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not content.strip():
            raise FileNotFoundError(f"Missing writing knowledge file: {path}")
        corpus_rows.append(
            {
                "doc_id": spec["doc_id"],
                "title": spec["title"],
                "content": content,
                "metadata": {"domain": spec["domain"], "source": "repo_knowledge"},
            }
        )

    if include_common_decoys:
        corpus_rows.extend(
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "content": d["content"],
                "metadata": {"domain": d["domain"], "source": "decoy"},
            }
            for d in _DECOY_SPECS
        )

    spec = yaml.safe_load(TASKS_SPEC.read_text(encoding="utf-8"))
    task_rows: list[dict] = []
    for task in list(spec.get("tasks") or []):
        rel = [str(x) for x in (task.get("relevant_doc_ids") or [])]
        task_rows.append(
            {
                "id": str(task["id"]),
                "domain": "writing",
                "query": str(task["query"]).strip(),
                "relevant_doc_ids": rel,
                "metadata": {
                    "benchmark": "writing",
                    "enrich_writing": bool(task.get("enrich_writing")),
                },
            }
        )

    corpus_path = NORMALIZED_DIR / "writing_corpus.jsonl"
    tasks_path = NORMALIZED_DIR / "writing_tasks.jsonl"
    _write_jsonl(corpus_path, corpus_rows)
    _write_jsonl(tasks_path, task_rows)
    return corpus_path, tasks_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert repo writing knowledge to normalized JSONL.")
    parser.add_argument(
        "--no-common-decoys",
        action="store_true",
        help="Omit common-domain decoy docs (skip domain-filter stress tasks)",
    )
    args = parser.parse_args()
    corpus_path, tasks_path = convert_writing(include_common_decoys=not args.no_common_decoys)
    print(f"Wrote corpus: {corpus_path}")
    print(f"Wrote tasks:  {tasks_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
