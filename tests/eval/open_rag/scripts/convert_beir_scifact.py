#!/usr/bin/env python3
"""Download BEIR SciFact and convert to project-normalized JSONL format.

Usage:
  pip install beir
  python tests/eval/open_rag/scripts/convert_beir_scifact.py --download
  python tests/eval/open_rag/scripts/convert_beir_scifact.py --max-queries 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

OPEN_RAG_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = OPEN_RAG_DIR / "raw" / "scifact"
NORMALIZED_DIR = OPEN_RAG_DIR / "normalized"
SCIFACT_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _download_raw(raw_dir: Path) -> Path:
    from beir import util

    raw_dir.mkdir(parents=True, exist_ok=True)
    return Path(util.download_and_unzip(SCIFACT_URL, str(raw_dir.parent)))


def _load_beir(data_path: Path, split: str = "test"):
    from beir.datasets.data_loader import GenericDataLoader

    return GenericDataLoader(data_folder=str(data_path)).load(split=split)


def convert_scifact(
    *,
    raw_dir: Path,
    output_dir: Path,
    split: str = "test",
    max_queries: int | None = None,
    download: bool = False,
) -> tuple[Path, Path]:
    data_path = raw_dir
    if download or not (raw_dir / "corpus.jsonl").exists():
        data_path = _download_raw(raw_dir)

    corpus_map, queries_map, qrels_map = _load_beir(data_path, split=split)

    query_items = list(queries_map.items())
    if max_queries is not None:
        query_items = query_items[: max(0, max_queries)]

    relevant_doc_ids: set[str] = set()
    for _qid, rel_docs in qrels_map.items():
        relevant_doc_ids.update(rel_docs.keys())
    for qid, _ in query_items:
        relevant_doc_ids.update(qrels_map.get(qid, {}).keys())

    corpus_rows: list[dict] = []
    for doc_id, doc in corpus_map.items():
        if doc_id not in relevant_doc_ids:
            continue
        title = str(doc.get("title") or "").strip()
        content = str(doc.get("text") or doc.get("content") or "").strip()
        corpus_rows.append({"doc_id": str(doc_id), "title": title, "content": content})

    task_rows: list[dict] = []
    for qid, query_text in query_items:
        rel = [str(did) for did, score in qrels_map.get(qid, {}).items() if int(score) > 0]
        if not rel:
            continue
        task_rows.append(
            {
                "id": f"beir-scifact-{qid}",
                "domain": "common",
                "query": str(query_text).strip(),
                "relevant_doc_ids": rel,
            }
        )

    corpus_path = output_dir / "scifact_corpus.jsonl"
    tasks_path = output_dir / "scifact_tasks.jsonl"
    _write_jsonl(corpus_path, corpus_rows)
    _write_jsonl(tasks_path, task_rows)
    return corpus_path, tasks_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert BEIR SciFact to normalized JSONL.")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="BEIR raw dataset directory")
    parser.add_argument("--output-dir", type=Path, default=NORMALIZED_DIR, help="Normalized output directory")
    parser.add_argument("--split", default="test", help="BEIR split (default: test)")
    parser.add_argument("--max-queries", type=int, default=None, help="Limit query count for quick samples")
    parser.add_argument("--download", action="store_true", help="Download SciFact if missing")
    args = parser.parse_args()

    try:
        corpus_path, tasks_path = convert_scifact(
            raw_dir=args.raw_dir,
            output_dir=args.output_dir,
            split=args.split,
            max_queries=args.max_queries,
            download=args.download,
        )
    except ImportError:
        print("Missing dependency: pip install beir", file=sys.stderr)
        return 1

    print(f"Wrote corpus: {corpus_path}")
    print(f"Wrote tasks:  {tasks_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
