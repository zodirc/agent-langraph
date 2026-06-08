"""Replay harness smoke + interrupt/resume library (WP-3.3)."""

from __future__ import annotations

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.interrupt_control import apply_interrupt_control
from app.services.structured_checkpoint import build_structured_checkpoint, restore_from_structured_checkpoint
from tests.eval.replay.harness import ReplayCase, assert_replay_dimensions


def _interrupt_runner(state: dict) -> dict:
    from app.services.context_budget import initialize_context_budget_buckets

    base = create_initial_state(input_payload=state.get("input_payload") or {})
    merged = merge_state(
        base,
        context_budget_buckets=initialize_context_budget_buckets(base),
        **{k: v for k, v in state.items() if k not in {"input_payload", "checkpoint"}},
    )
    return apply_interrupt_control(merged)


def _resume_runner(state: dict) -> dict:
    from app.services.context_budget import initialize_context_budget_buckets

    ckpt = state.get("checkpoint") or build_structured_checkpoint(create_initial_state())
    base = merge_state(
        create_initial_state(),
        input_payload={"structured_checkpoint": ckpt},
        event_type="resume",
        context_budget_buckets=initialize_context_budget_buckets(create_initial_state()),
    )
    after_interrupt = apply_interrupt_control(base)
    return restore_from_structured_checkpoint(after_interrupt)


INTERRUPT_RESUME_CASES = [
    ReplayCase("interrupt_planning", "regression", {"event_type": "interrupt", "current_node": "incremental_planning"}, {}),
    ReplayCase("interrupt_retrieval", "regression", {"event_type": "interrupt", "current_node": "retrieval"}, {}),
    ReplayCase("interrupt_tool", "regression", {"event_type": "interrupt", "current_node": "tool_execution"}, {}),
    ReplayCase("interrupt_writing", "regression", {"event_type": "interrupt", "current_node": "reasoning_or_writing"}, {}),
    ReplayCase(
        "interrupt_abort",
        "boundary",
        {
            "event_type": "interrupt",
            "interrupt_context": {"abort_requested": True, "cancel_requested": True},
        },
        {"status": TaskStatus.CANCELLED.value},
    ),
    ReplayCase(
        "resume_checkpoint",
        "regression",
        {
            "checkpoint": build_structured_checkpoint(
                merge_state(create_initial_state(), execution_version=5, plan=["step"])
            ),
        },
        {"execution_version": 5},
    ),
]


def test_replay_interrupt_resume_library():
    runners = {
        "interrupt_planning": _interrupt_runner,
        "interrupt_retrieval": _interrupt_runner,
        "interrupt_tool": _interrupt_runner,
        "interrupt_writing": _interrupt_runner,
        "interrupt_abort": _interrupt_runner,
        "resume_checkpoint": _resume_runner,
    }
    for case in INTERRUPT_RESUME_CASES:
        runner = runners[case.name]
        final = runner(case.state)
        for key, expected in case.assertions.items():
            assert final.get(key) == expected, f"{case.name}: {key}"
        assert_replay_dimensions(
            case.name,
            final,
            required={"routing", "interrupt", "budget"},
        )


def test_replay_case_library_dirs_exist():
    from tests.eval.replay.harness import CASE_LIBRARY_DIRS

    assert len(CASE_LIBRARY_DIRS) == 4
