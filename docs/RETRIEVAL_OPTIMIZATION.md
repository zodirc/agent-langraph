# Knowledge Retrieval Optimization

This document describes the retrieval optimizations applied to the knowledge RAG pipeline.

## What Changed

- Increased default knowledge `top_k` from `3` to `8`.
- Enabled RAG rerank by default (`rag.rerank_enabled: true`, lexical backend).
- Upgraded keyword retrieval from simple substring matching to BM25-style scoring.
- Added CJK-aware tokenization (Chinese-friendly token extraction + bigrams).
- Increased hybrid retrieval candidate pool using `rag.fetch_k_multiplier`.
- Applied tenant/domain filtering earlier in vector retrieval flow.
- Added diversity guard with `rag.max_chunks_per_doc` to reduce repeated chunks from one source.
- **Chunk-first indexing**: long documents are split on ingest via `app/services/knowledge_chunker.py`; vector + BM25 search run at chunk granularity; optional adjacent-chunk expansion after hybrid merge.

## Why These Changes

- Small `top_k` can miss relevant context before rerank.
- Pure substring matching is weak for Chinese and long-form text.
- Hybrid merge quality depends on candidate breadth.
- Post-merge filtering can waste candidate slots.
- Repeated chunks from one document reduce answer coverage.

## Current Retrieval Pipeline

1. Build retrieval query (writing tasks may enrich query — see `writing_knowledge.py`).
2. Retrieve vector candidates at **chunk** level (`fetch_k`).
3. Retrieve BM25 keyword candidates at **chunk** level (`fetch_k`).
4. Merge with RRF.
5. Normalize metadata (`chunk_index`, `parent_doc_id`, `section_title`).
6. Optional rerank (default enabled, lexical backend).
7. Apply per-document diversity cap (`max_chunks_per_doc` groups by `parent_doc_id`).
8. Optional adjacent-chunk expansion (`chunk_adjacency_enabled`).
9. Return final `top_k`.

## Chunk-First Ingest

On `POST /knowledge/documents` or builtin seed (`ensure_builtin_knowledge`):

- Content shorter than `rag.chunk_min_chars` → single searchable row (unchanged behavior).
- Longer content → parent catalog row (full text, not searched) + N chunk rows indexed in SQLite/Chroma/Qdrant.
- Chunk embed text includes document title and section heading for better semantic and lexical recall.

Disable per deploy: `rag.chunk_enabled: false`. Disable per document: `metadata.skip_chunking: true`.

## New/Updated Configs

```yaml
knowledge:
  top_k: 8
  embedding_model: local_minilm
  local_embedding_model_name: sentence-transformers/all-MiniLM-L6-v2
  local_embedding_device: cpu

rag:
  rerank_enabled: true
  rerank_backend: lexical
  rerank_candidate_k: 24
  fetch_k_multiplier: 4
  bm25_k1: 1.5
  bm25_b: 0.75
  max_chunks_per_doc: 2
  chunk_enabled: true
  chunk_min_chars: 600
  chunk_max_chars: 1200
  chunk_overlap_chars: 150
  chunk_adjacency_enabled: true
  chunk_adjacency_radius: 1
```

## Local Embedding in Docker

- Docker default now uses `knowledge.embedding_model: local_minilm`.
- Runtime loads `sentence-transformers/all-MiniLM-L6-v2` in-process.
- Model cache path can be persisted by setting:
  - `knowledge.local_embedding_cache_dir: /data/models/sentence-transformers`

## Tuning Guide

- **Recall too low**: increase `knowledge.top_k` and `rag.fetch_k_multiplier`.
- **Too many irrelevant hits**: lower `knowledge.top_k`, keep rerank enabled.
- **Over-concentrated sources**: reduce `rag.max_chunks_per_doc`.
- **Keyword overweight**: lower `rag.bm25_k1` slightly (for example `1.2`).
- **Long docs dominate**: increase `rag.bm25_b` toward `0.8`.

## Safety and Rollback

To rollback behavior quickly:

- Set `rag.rerank_enabled: false`.
- Reduce `knowledge.top_k` back to `3`.
- Set `rag.fetch_k_multiplier: 1`.

These settings restore a lighter retrieval path without code changes.

## Tests Added

- Chinese keyword retrieval coverage in `test_knowledge_store`.
- Parent document diversity cap behavior in `test_knowledge_store`.
- Structured chunking in `tests/services/test_knowledge_chunker.py`.
- Chunked upsert, reassembly, and delete in `tests/services/test_knowledge_chunked_upsert.py`.

## Tuning Chunking

- **Sections still too broad**: lower `chunk_max_chars` or `chunk_max_chars_writing`.
- **Too many near-duplicate hits**: reduce `chunk_overlap_chars` or `max_chunks_per_doc`.
- **Missing local context in answers**: enable `chunk_adjacency_enabled` or increase `chunk_adjacency_radius`.
- **Golden / short-doc tests**: keep content under `chunk_min_chars` or set `skip_chunking` in metadata.
- **Code retrieval lacks precision**: keep `domain=code` metadata accurate; code domain chunking now prefers symbol/config boundaries (`def/class/function/UPPER_CASE_KEY`) before fallback paragraph splitting.

## Offline Domain Evaluation

Use `app/services/rag_domain_eval.py` for bucketed offline evaluation:

- Input task schema: `id`, `domain`, `query`, `relevant_doc_ids`.
- Domains are normalized to `writing`, `code`, `common` (`unknown` falls back to `common`).
- Output includes:
  - `summary`: global averages (`recall@k`, `mrr`, `ndcg@k`, `context_redundancy`)
  - `per_domain`: same metrics per domain bucket
  - `per_task`: raw per-task outputs and retrieved ids
- `context_redundancy` is a lexical heuristic ratio: among retrieved hits, how many share zero tokens with the query (lower is better).

