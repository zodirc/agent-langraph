#!/usr/bin/env bash
# Snapshot open RAG eval reports into tests/eval/reports/baselines/ (gitignored).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path

reports = Path("tests/eval/reports")
baselines = reports / "baselines"
baselines.mkdir(parents=True, exist_ok=True)

MAPPING = [
    ("qrecc_test_1000_eval.json", "qrecc_closed_corpus_1000.json"),
    ("qrecc_test_eval.json", "qrecc_closed_corpus_10.json"),
    ("writing_eval.json", "writing_guidelines_v1.json"),
    ("qrecc_eval.json", "qrecc_sample_2.json"),
    ("scifact_eval.json", "scifact_sample_2.json"),
]

runs = []
for src_name, dst_name in MAPPING:
    src = reports / src_name
    if not src.is_file():
        continue
    data = json.loads(src.read_text(encoding="utf-8"))
    (baselines / dst_name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    runs.append(
        {
            "baseline_file": f"baselines/{dst_name}",
            "source_report": src_name,
            "benchmark": data.get("benchmark"),
            "top_k": data.get("top_k"),
            "imported_docs": data.get("imported_docs"),
            "task_count": data.get("task_count"),
            "summary": data.get("summary"),
            "per_domain": data.get("per_domain"),
        }
    )

comparison = {
    "snapshot_at": datetime.now(timezone.utc).isoformat(),
    "note": "Local baselines for before/after RAG tuning; directory is gitignored.",
    "runs": runs,
}
path = baselines / "comparison.json"
path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {path} ({len(runs)} runs)")
PY
