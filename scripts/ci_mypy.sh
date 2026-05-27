#!/usr/bin/env bash
# Strict mypy on app/runtime (AgentState graph boundaries)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PY" ]]; then PY=python3; fi

if ! "$PY" -m mypy --version >/dev/null 2>&1; then
  echo "Installing mypy..."
  "$PY" -m pip install -q "mypy>=1.8.0"
fi

echo "==> mypy strict: app/runtime"
exec "$PY" -m mypy app/runtime --config-file mypy.ini "$@"
