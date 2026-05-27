"""Eval suite hooks: baseline capture and regression gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.eval_metrics import (
    clear,
    compare_baseline,
    load_baseline,
    save_baseline,
    snapshot,
)

_BASELINE_DEFAULT = Path(__file__).parent / "baseline.json"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("eval", "Golden eval regression")
    group.addoption(
        "--baseline",
        action="store",
        default=str(_BASELINE_DEFAULT),
        help="Path to baseline.json for golden metrics",
    )
    group.addoption(
        "--update-baseline",
        action="store_true",
        default=False,
        help="Write current metrics to baseline file after run",
    )
    group.addoption(
        "--fail-on-regression",
        action="store",
        default="0.05",
        help="Max fractional metric drop vs baseline (e.g. 0.05 = 5%%)",
    )
    group.addoption(
        "--rag-min-recall",
        action="store",
        default=None,
        help="Fail if mean RAG recall@5 drops below this (e.g. 0.8)",
    )
    group.addoption(
        "--rag-min-mrr",
        action="store",
        default=None,
        help="Fail if mean RAG MRR drops below this (e.g. 0.5)",
    )
    group.addoption(
        "--rag-min-faithfulness",
        action="store",
        default=None,
        help="Fail if mean RAG faithfulness score drops below this (e.g. 0.75)",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    clear()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not session.config.getoption("--baseline", default=None):
        return
    baseline_path = Path(session.config.getoption("--baseline"))
    current = snapshot()
    if not current:
        return
    if session.config.getoption("--update-baseline"):
        save_baseline(baseline_path, current)
        return
    if exitstatus != 0:
        return
    baseline = load_baseline(baseline_path)
    if not baseline:
        return
    try:
        max_reg = float(session.config.getoption("--fail-on-regression"))
    except ValueError:
        max_reg = 0.05
    errors = compare_baseline(current, baseline, max_regression=max_reg)

    from tests.eval.eval_thresholds import check_rag_thresholds

    def _opt_float(name: str) -> float | None:
        raw = session.config.getoption(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    threshold_errors = check_rag_thresholds(
        current,
        min_recall=_opt_float("--rag-min-recall"),
        min_mrr=_opt_float("--rag-min-mrr"),
        min_faithfulness=_opt_float("--rag-min-faithfulness"),
    )
    errors.extend(threshold_errors)

    if errors:
        session.exitstatus = 1
        terminal = session.config.pluginmanager.get_plugin("terminalreporter")
        if terminal:
            terminal.write_line("Golden eval regression failures:")
            for err in errors:
                terminal.write_line(f"  - {err}")
