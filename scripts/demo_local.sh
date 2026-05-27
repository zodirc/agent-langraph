#!/usr/bin/env bash
# Zero-API-key local demo: venv, smoke tests, CLI single-turn task.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
fi

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Creating virtualenv..."
  "$PY" -m venv .venv
  PY="$ROOT/.venv/bin/python"
fi

export MODEL_ENABLED=false
export ANTHROPIC_API_KEY=
export VOYAGE_API_KEY=

echo "==> Installing dependencies (if needed)"
"$PY" -m pip install -q -r requirements.txt

echo "==> Smoke tests (no real LLM)"
"$PY" -m pytest \
  tests/integration/test_graph_flow.py \
  tests/services/test_context_compressor.py \
  tests/services/test_graph_execution_pool.py \
  tests/services/test_circuit_breaker.py \
  tests/services/test_checkpoint_recovery.py \
  tests/eval/test_golden_eval.py \
  -q --tb=no

echo "==> CLI local run (stub LLM, MODEL_ENABLED=false)"
"$PY" -m app.cli --local run "Explain what an agent runtime does in one sentence."

echo ""
echo "Demo OK. Next: uvicorn app.main:app --port 8000  →  http://localhost:8000/"
