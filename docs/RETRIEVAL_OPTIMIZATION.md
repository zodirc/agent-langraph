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

## Why These Changes

- Small `top_k` can miss relevant context before rerank.
- Pure substring matching is weak for Chinese and long-form text.
- Hybrid merge quality depends on candidate breadth.
- Post-merge filtering can waste candidate slots.
- Repeated chunks from one document reduce answer coverage.

## Current Retrieval Pipeline

1. Build retrieval query.
2. Retrieve vector candidates (`fetch_k`).
3. Retrieve BM25 keyword candidates (`fetch_k`).
4. Merge with RRF.
5. Normalize metadata.
6. Optional rerank (default enabled, lexical backend).
7. Apply per-document diversity cap.
8. Return final `top_k`.

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

