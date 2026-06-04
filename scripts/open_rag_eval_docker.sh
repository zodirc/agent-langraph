#!/usr/bin/env bash
# Run open RAG benchmark inside the agent container (local_minilm + config.docker.yaml).
# 若刚改过 compose 卷挂载，需先：docker compose up -d --force-recreate agent
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BENCHMARK="${BENCHMARK:-qrecc}"
MAX_QUERIES="${MAX_QUERIES:-1000}"
SPLIT="${SPLIT:-test}"
OUTPUT="${OUTPUT:-}"

if [ "$BENCHMARK" = "writing" ]; then
  echo "==> convert writing knowledge"
  docker compose exec agent python tests/eval/open_rag/scripts/convert_writing_knowledge.py
  OUTPUT="${OUTPUT:-tests/eval/reports/writing_eval.json}"
  docker compose exec agent python tests/eval/run_open_rag_eval.py \
    --benchmark writing \
    --corpus tests/eval/open_rag/normalized/writing_corpus.jsonl \
    --tasks tests/eval/open_rag/normalized/writing_tasks.jsonl \
    --output "$OUTPUT"
  echo "==> done: ${ROOT}/${OUTPUT}"
  exit 0
fi

OUTPUT="${OUTPUT:-tests/eval/reports/qrecc_test_${MAX_QUERIES}_eval.json}"

echo "==> convert QReCC (split=${SPLIT}, max_queries=${MAX_QUERIES})"
docker compose exec agent python tests/eval/open_rag/scripts/convert_qrecc.py \
  --split "$SPLIT" \
  --max-queries "$MAX_QUERIES"

echo "==> run open RAG eval -> ${OUTPUT}"
docker compose exec agent python tests/eval/run_open_rag_eval.py \
  --benchmark qrecc \
  --corpus "tests/eval/open_rag/normalized/qrecc_${SPLIT}_corpus.jsonl" \
  --tasks "tests/eval/open_rag/normalized/qrecc_${SPLIT}_rewrite_tasks.jsonl" \
  --output "$OUTPUT"

echo "==> done: ${ROOT}/${OUTPUT}"
