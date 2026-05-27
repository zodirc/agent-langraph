"""RAG eval aggregate threshold gates for CI."""

from __future__ import annotations


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _is_positive_faithfulness_task(task_id: str) -> bool:
    """Tasks that should score high; negative controls are excluded from the mean."""
    if "unfaithful" in task_id or task_id.endswith("_bad"):
        return False
    return "faithful" in task_id or "faithfulness" in task_id


def check_rag_thresholds(
    snapshot: dict[str, dict[str, float]],
    *,
    min_recall: float | None = None,
    min_mrr: float | None = None,
    min_faithfulness: float | None = None,
) -> list[str]:
    """Return threshold violation messages (empty if all pass)."""
    errors: list[str] = []

    recall_scores: list[float] = []
    mrr_scores: list[float] = []
    faith_scores: list[float] = []

    for task_id, metrics in snapshot.items():
        if "recall@5" in metrics:
            recall_scores.append(float(metrics["recall@5"]))
        elif "recall" in task_id and "passed" in metrics:
            recall_scores.append(float(metrics["passed"]))

        if "mrr" in metrics:
            mrr_scores.append(float(metrics["mrr"]))
        elif task_id.endswith("_mrr") or "mrr" in task_id:
            if "passed" in metrics:
                mrr_scores.append(float(metrics["passed"]))

        if not _is_positive_faithfulness_task(task_id):
            continue
        for key, val in metrics.items():
            if key.endswith("_faithfulness") or key in ("faithfulness", "score"):
                faith_scores.append(float(val))

    if min_recall is not None and recall_scores:
        avg = _mean(recall_scores)
        if avg < min_recall:
            errors.append(f"mean recall {avg:.3f} < min {min_recall}")

    if min_mrr is not None and mrr_scores:
        avg = _mean(mrr_scores)
        if avg < min_mrr:
            errors.append(f"mean MRR {avg:.3f} < min {min_mrr}")

    if min_faithfulness is not None and faith_scores:
        avg = _mean(faith_scores)
        if avg < min_faithfulness:
            errors.append(f"mean faithfulness {avg:.3f} < min {min_faithfulness}")

    return errors


def check_context_compress_thresholds(
    snapshot: dict[str, dict[str, float]],
    *,
    min_ratio: float | None = None,
) -> list[str]:
    """Fail when recorded context compression ratios fall below min_ratio."""
    if min_ratio is None:
        return []
    errors: list[str] = []
    ratios: list[float] = []
    for task_id, metrics in snapshot.items():
        if "compress" not in task_id and "ratio" not in metrics:
            continue
        if "ratio" in metrics:
            ratios.append(float(metrics["ratio"]))
    if ratios:
        avg = _mean(ratios)
        if avg < min_ratio:
            errors.append(f"mean context compress ratio {avg:.3f} < min {min_ratio}")
    return errors
