#!/usr/bin/env python3
"""Writing RAG A/B eval: compliance with vs without injected guidelines.

Usage:
  python tests/eval/run_writing_rag_ab_eval.py
  python tests/eval/run_writing_rag_ab_eval.py --output tests/eval/reports/writing_rag_ab.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REPORTS_DIR = Path(__file__).parent / "reports"
TASKS_YAML = Path(__file__).parent / "open_rag" / "writing_tasks.yaml"

# Representative samples for offline A/B (deterministic; no live LLM required).
_SAMPLE_WITH_RAG = (
    "第三章\n\n"
    "雨落在瓦当上，声音细碎而均匀。林远推门时，木门发出一声干涩的呻吟，"
    "檐角铜铃在风里轻轻晃了一下。\n\n"
    "「你来了。」她没有回头，只把信笺压进袖里，像压住一段尚未开口的往事。\n\n"
    "林远停在门槛外，目光掠过案上未干的墨迹。\n\n"
    "（第3章完）"
)
_SAMPLE_WITHOUT_RAG = (
    "第三章\n\n"
    "众所周知，在这个充满未知的世界里，林远推开了门。\n\n"
    "综上所述，她把信笺收了起来，仿佛时间都静止了。\n\n"
    "作为AI，我根据您的要求续写了本章。"
)


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("tasks") or [])


def _guidelines_for_task(task: dict[str, Any]) -> str:
    doc_ids = [str(x) for x in (task.get("relevant_doc_ids") or [])]
    chunks = []
    for doc_id in doc_ids:
        if "prose" in doc_id:
            chunks.append(
                f"[{doc_id}]\n对话单独成行；段落之间空一行；章末使用（第N章完）。"
            )
        else:
            chunks.append(f"[{doc_id}]\n场景锚点；伏笔；剧情连贯；避免空洞寒暄。")
    return "\n\n---\n\n".join(chunks)


def run_ab_eval(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.writing_rag_eval import (
        compare_rag_ab_delta,
        score_writing_compliance,
    )

    per_task: list[dict[str, Any]] = []
    snapshot: dict[str, dict[str, float]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        guidelines = _guidelines_for_task(task)
        with_rag = score_writing_compliance(
            _SAMPLE_WITH_RAG,
            guidelines_excerpt=guidelines,
        )
        without_rag = score_writing_compliance(_SAMPLE_WITHOUT_RAG)
        delta = compare_rag_ab_delta(with_rag, without_rag)
        row = {
            "id": task_id,
            "with_rag": with_rag,
            "without_rag": without_rag,
            "delta": delta,
        }
        per_task.append(row)
        snapshot[f"writing_rag_ab_{task_id}"] = {
            **with_rag,
            **{f"{k}": v for k, v in delta.items()},
        }

    overall = [t["with_rag"]["overall"] for t in per_task]
    deltas = [t["delta"]["overall_delta"] for t in per_task]
    summary = {
        "task_count": len(per_task),
        "mean_overall_with_rag": sum(overall) / len(overall) if overall else 0.0,
        "mean_overall_delta": sum(deltas) / len(deltas) if deltas else 0.0,
    }
    return {"summary": summary, "per_task": per_task, "snapshot": snapshot}


def main() -> int:
    parser = argparse.ArgumentParser(description="Writing RAG A/B compliance eval")
    parser.add_argument("--tasks", type=Path, default=TASKS_YAML)
    parser.add_argument("--output", type=Path, default=REPORTS_DIR / "writing_rag_ab.json")
    parser.add_argument("--min-overall", type=float, default=0.5)
    parser.add_argument("--min-rag-delta", type=float, default=0.15)
    args = parser.parse_args()

    tasks = _load_tasks(args.tasks)
    report = run_ab_eval(tasks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[writing-rag-ab] wrote {args.output}", flush=True)

    from tests.eval.eval_thresholds import check_writing_compliance_thresholds

    errors = check_writing_compliance_thresholds(
        report.get("snapshot") or {},
        min_overall=args.min_overall,
        min_rag_delta=args.min_rag_delta,
    )
    if errors:
        for err in errors:
            print(f"[writing-rag-ab] THRESHOLD FAIL: {err}", flush=True)
        return 1
    print("[writing-rag-ab] thresholds OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
