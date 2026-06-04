#!/usr/bin/env python3
"""Download QReCC and convert to project-normalized JSONL for closed-corpus RAG eval.

QReCC's full benchmark retrieves from ~54M web passages (Zenodo). For local RAG
benchmarking we use a closed-corpus proxy: each turn's gold Answer becomes a
document, and retrieval is evaluated with Rewrite / Question / conversational query.

Usage:
  python tests/eval/open_rag/scripts/convert_qrecc.py --download --split test
  python tests/eval/open_rag/scripts/convert_qrecc.py --download --split test --max-queries 50
  python tests/eval/open_rag/scripts/convert_qrecc.py --query-mode conversational
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

OPEN_RAG_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = OPEN_RAG_DIR / "raw" / "qrecc"
NORMALIZED_DIR = OPEN_RAG_DIR / "normalized"
QRECC_ZIP_URL = "https://raw.githubusercontent.com/apple/ml-qrecc/main/dataset/qrecc_data.zip"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _doc_id(conversation_no: int, turn_no: int) -> str:
    return f"qrecc-{conversation_no}-{turn_no}"


def _title_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "").strip("/")
    if not path:
        return "QReCC Answer"
    slug = path.split("/")[-1].replace("_", " ").replace("-", " ").strip()
    return slug[:120] or "QReCC Answer"


def _build_query(row: dict[str, Any], mode: str) -> str:
    if mode == "rewrite":
        return str(row.get("Rewrite") or row.get("Question") or "").strip()
    if mode == "question":
        return str(row.get("Question") or "").strip()
    if mode == "conversational":
        parts = [str(x).strip() for x in (row.get("Context") or []) if str(x).strip()]
        question = str(row.get("Question") or "").strip()
        if question:
            parts.append(question)
        return "\n".join(parts)
    raise ValueError(f"Unknown query mode: {mode}")


def _download_and_extract(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / "qrecc_data.zip"
    if not zip_path.exists():
        print(f"Downloading {QRECC_ZIP_URL} ...")
        urlretrieve(QRECC_ZIP_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(raw_dir)


def _load_split(split: str, raw_dir: Path, download: bool) -> list[dict[str, Any]]:
    json_name = f"qrecc_{split}.json"
    json_path = raw_dir / json_name
    if download or not json_path.exists():
        _download_and_extract(raw_dir)

    if not json_path.exists():
        raise FileNotFoundError(f"Missing {json_path}; run with --download")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {json_path}")
    return [dict(item) for item in data]


def convert_qrecc(
    *,
    raw_dir: Path,
    output_dir: Path,
    split: str = "test",
    query_mode: str = "rewrite",
    max_queries: int | None = None,
    distractor_docs: int = 200,
    download: bool = False,
) -> tuple[Path, Path]:
    rows = _load_split(split, raw_dir, download=download)

    task_candidates: list[dict[str, Any]] = []
    corpus_by_id: dict[str, dict] = {}
    for row in rows:
        answer = str(row.get("Answer") or "").strip()
        if not answer:
            continue
        conv = int(row["Conversation_no"])
        turn = int(row["Turn_no"])
        did = _doc_id(conv, turn)
        corpus_by_id[did] = {
            "doc_id": did,
            "title": _title_from_url(str(row.get("Answer_URL") or "")),
            "content": answer,
            "metadata": {
                "answer_url": row.get("Answer_URL"),
                "conversation_no": conv,
                "turn_no": turn,
            },
        }
        query = _build_query(row, query_mode)
        if not query:
            continue
        task_candidates.append(
            {
                "id": did,
                "domain": "common",
                "query": query,
                "relevant_doc_ids": [did],
                "metadata": {
                    "benchmark": "qrecc",
                    "query_mode": query_mode,
                    "conversation_no": conv,
                    "turn_no": turn,
                    "question": row.get("Question"),
                    "rewrite": row.get("Rewrite"),
                    "context": row.get("Context") or [],
                    "answer_url": row.get("Answer_URL"),
                    "conversation_source": row.get("Conversation_source"),
                },
            }
        )

    if max_queries is not None:
        task_rows = task_candidates[: max(0, max_queries)]
    else:
        task_rows = task_candidates

    if max_queries is not None:
        keep_ids = {rid for task in task_rows for rid in task["relevant_doc_ids"]}
        extra_ids = [did for did in corpus_by_id if did not in keep_ids]
        for did in extra_ids[: max(0, distractor_docs)]:
            keep_ids.add(did)
        corpus_rows = [corpus_by_id[did] for did in keep_ids if did in corpus_by_id]
    else:
        corpus_rows = list(corpus_by_id.values())

    corpus_path = output_dir / f"qrecc_{split}_corpus.jsonl"
    tasks_path = output_dir / f"qrecc_{split}_{query_mode}_tasks.jsonl"
    _write_jsonl(corpus_path, corpus_rows)
    _write_jsonl(tasks_path, task_rows)
    return corpus_path, tasks_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert QReCC to normalized JSONL.")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=NORMALIZED_DIR)
    parser.add_argument("--split", choices=("train", "test"), default="test")
    parser.add_argument(
        "--query-mode",
        choices=("rewrite", "question", "conversational"),
        default="rewrite",
        help="Which query text to feed the retriever (default: rewrite)",
    )
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument(
        "--distractor-docs",
        type=int,
        default=200,
        help="When --max-queries is set, add this many extra corpus docs as distractors",
    )
    parser.add_argument("--download", action="store_true", help="Download/refresh from Apple GitHub")
    args = parser.parse_args()

    try:
        corpus_path, tasks_path = convert_qrecc(
            raw_dir=args.raw_dir,
            output_dir=args.output_dir,
            split=args.split,
            query_mode=args.query_mode,
            max_queries=args.max_queries,
            distractor_docs=args.distractor_docs,
            download=args.download,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Wrote corpus: {corpus_path}")
    print(f"Wrote tasks:  {tasks_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
