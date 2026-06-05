#!/usr/bin/env bash
# Golden eval CI gate (Ch19) — unit + integration + RAG suites
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PY" ]]; then PY=python3; fi

SUITE="${SUITE:-all}"
BASELINE="${BASELINE:-tests/eval/baseline.json}"
FAIL_REG="${FAIL_ON_REGRESSION:-0.05}"
RAG_MIN_RECALL="${RAG_MIN_RECALL:-0.8}"
RAG_MIN_MRR="${RAG_MIN_MRR:-0.5}"
RAG_MIN_FAITHFULNESS="${RAG_MIN_FAITHFULNESS:-0.75}"

_rag_flags=(
  "--baseline=tests/eval/rag_baseline.json"
  "--fail-on-regression=${FAIL_REG}"
  "--rag-min-recall=${RAG_MIN_RECALL}"
  "--rag-min-mrr=${RAG_MIN_MRR}"
  "--rag-min-faithfulness=${RAG_MIN_FAITHFULNESS}"
)

case "$SUITE" in
  unit)
    echo "==> eval suite: unit"
    exec "$PY" -m pytest tests/eval/test_golden_eval.py \
      --baseline="$BASELINE" \
      --fail-on-regression="$FAIL_REG" \
      "$@"
    ;;
  integration)
    echo "==> eval suite: integration"
    exec "$PY" -m pytest tests/eval/test_integration_golden.py \
      --baseline="tests/eval/integration_baseline.json" \
      --fail-on-regression="$FAIL_REG" \
      "$@"
    ;;
  engineering)
    echo "==> eval suite: engineering (golden + unit + optional live E2E)"
    "$PY" -m pytest \
      tests/eval/test_integration_golden.py \
      -k "engineering or qa_mode" \
      --baseline="tests/eval/integration_baseline.json" \
      --fail-on-regression="$FAIL_REG" \
      "$@"
    "$PY" -m pytest \
      tests/services/test_mode_router.py \
      tests/services/test_mode_session_rules.py \
      tests/services/test_mode_session_switch.py \
      tests/services/test_mode_contract_apply.py \
      tests/runtime/test_mode_routing.py \
      tests/services/test_engineering_bounded_limits.py \
      tests/services/test_engineering_repair.py \
      tests/services/test_engineering_execution.py \
      tests/services/test_project_verify_security.py \
      tests/services/test_mission_routing.py \
      tests/config/test_mode_config_parity.py \
      tests/integration/test_engineering_mode_flow.py \
      -q \
      "$@"
    if [[ -n "${ENGINEERING_E2E_LIVE:-}" ]]; then
      echo "==> engineering live E2E (ENGINEERING_E2E_LIVE=1)"
      "$PY" -m pytest tests/e2e/test_engineering_mode_live.py -v "$@"
    else
      echo "==> skip live E2E (set ENGINEERING_E2E_LIVE=1 to enable)"
    fi
    ;;
  oma)
    echo "==> eval suite: oma (ADR-001 M8 golden)"
    exec "$PY" -m pytest tests/integration/test_mission_oma_golden.py tests/services/test_mission_oma.py -q \
      "$@"
    ;;
  intent_observation)
    echo "==> eval suite: intent_observation"
    exec "$PY" -m pytest \
      tests/services/test_intent_observation.py \
      tests/eval/test_intent_observation_golden.py \
      tests/services/test_pre_planning.py \
      -q \
      "$@"
    ;;
  rag)
    echo "==> eval suite: rag (thresholds: recall>=${RAG_MIN_RECALL}, mrr>=${RAG_MIN_MRR}, faithfulness>=${RAG_MIN_FAITHFULNESS})"
    exec "$PY" -m pytest \
      tests/eval/test_rag_golden.py \
      tests/eval/test_rag_pipeline.py \
      tests/eval/test_rag_faithfulness_llm.py \
      "${_rag_flags[@]}" \
      "$@"
    ;;
  all)
    echo "==> eval suite: all"
    "$PY" -m pytest tests/eval/test_golden_eval.py \
      --baseline="$BASELINE" \
      --fail-on-regression="$FAIL_REG" \
      "$@"
    "$PY" -m pytest tests/eval/test_integration_golden.py \
      --baseline="tests/eval/integration_baseline.json" \
      --fail-on-regression="$FAIL_REG" \
      "$@"
    echo "==> eval suite: oma (ADR-001 M8)"
    "$PY" -m pytest tests/integration/test_mission_oma_golden.py tests/services/test_mission_oma.py -q \
      "$@"
    echo "==> eval suite: intent_observation"
    "$PY" -m pytest \
      tests/services/test_intent_observation.py \
      tests/eval/test_intent_observation_golden.py \
      -q \
      "$@"
    "$PY" -m pytest \
      tests/eval/test_rag_golden.py \
      tests/eval/test_rag_pipeline.py \
      tests/eval/test_rag_faithfulness_llm.py \
      tests/eval/test_eval_thresholds.py \
      "${_rag_flags[@]}" \
      "$@"
    ;;
  *)
    echo "Unknown SUITE=$SUITE (use unit|integration|engineering|oma|intent_observation|rag|all)" >&2
    exit 1
    ;;
esac
