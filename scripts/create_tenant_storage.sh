#!/usr/bin/env bash
# Provision or remove tenant-isolated storage (SQLite file or PG schema).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PY" ]]; then PY=python3; fi

ACTION="${1:-}"
TENANT="${2:-}"

if [[ -z "$ACTION" || -z "$TENANT" ]]; then
  echo "Usage: $0 create|drop <tenant_id>" >&2
  exit 1
fi

case "$ACTION" in
  create)
    exec "$PY" -c "
from app.services.tenant_storage import create_tenant_storage
import json
print(json.dumps(create_tenant_storage('${TENANT}'), indent=2))
"
    ;;
  drop)
    exec "$PY" -c "
from app.services.tenant_storage import drop_tenant_storage
import json
print(json.dumps(drop_tenant_storage('${TENANT}'), indent=2))
"
    ;;
  *)
    echo "Unknown action: $ACTION (use create|drop)" >&2
    exit 1
    ;;
esac
