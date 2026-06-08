"""Static control-plane guard checks (optimization.md §6 / §7 / Phase D)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "web" / "static" / "app.js"
TASK_API = ROOT / "app" / "api" / "task_api.py"
EVENT_CLASSIFICATION = ROOT / "app" / "services" / "event_classification.py"
SESSION_CONTROLLER = ROOT / "app" / "services" / "session_controller.py"
PROGRESS_EVALUATOR = ROOT / "app" / "services" / "progress_evaluator.py"

FORBIDDEN_FRONTEND_PATTERNS = (
    "shouldSteerReplan",
    "shouldSteerUserMessage",
    "isContinueWritingGoal",
    "runResumeStream",
    "runSupersedeStream",
    "/steer/stream",
    "/resume/stream",
    "/supersede/stream",
    "preempt:",
    "replace_goal:",
    "replaceGoal:",
)

REQUIRED_FRONTEND_PATTERNS = (
    "sendMessage",
    "fsm_state",
    "/message/stream",
)

FORBIDDEN_TASK_API_PATTERNS = (
    '"/{task_id}/steer/stream"',
    '"/{task_id}/resume/stream"',
    '"/{task_id}/supersede/stream"',
    '"/{task_id}/steer"',
    '"/{task_id}/resume"',
    '"/{task_id}/supersede"',
    '@router.post("/stream"',
    "await /supersede",
)

FORBIDDEN_EVENT_CLASSIFICATION_PATTERNS = (
    'payload.get("preempt")',
    'payload.get("replace_goal")',
    'payload.get("require_planning_after_steer")',
    'payload.get("steer_replan_mode")',
)

FORBIDDEN_PROGRESS_EVALUATOR_PATTERNS = (
    "await /supersede",
    'TaskStatus.MISSION_PAUSED.value',
)

FORBIDDEN_ROUTING_TASKSTATUS_FILES = (
    ROOT / "app" / "runtime" / "planning_gate_router.py",
    ROOT / "app" / "runtime" / "router.py",
    ROOT / "app" / "services" / "session_fsm.py",
    ROOT / "app" / "services" / "turn_guard.py",
)

FORBIDDEN_ROUTING_TASKSTATUS_PATTERNS = (
    "TaskStatus.MISSION",
    "TaskStatus.FAILED",
    "TaskStatus.TOOL_FAILED",
    "TaskStatus.WRITTEN",
    "TaskStatus.PLANNED",
)


def test_frontend_no_legacy_routing_heuristics():
    text = APP_JS.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_FRONTEND_PATTERNS:
        assert pattern not in text, f"legacy control-plane pattern still in app.js: {pattern}"


def test_frontend_unified_ingress_present():
    text = APP_JS.read_text(encoding="utf-8")
    for pattern in REQUIRED_FRONTEND_PATTERNS:
        assert pattern in text, f"missing unified ingress pattern in app.js: {pattern}"


def test_task_api_no_removed_stream_endpoints():
    text = TASK_API.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_TASK_API_PATTERNS:
        assert pattern not in text, f"removed legacy endpoint still in task_api.py: {pattern}"


def test_task_api_no_sync_mission_wrappers():
    text = TASK_API.read_text(encoding="utf-8")
    assert "run_message_sync" not in text


def test_event_classification_no_client_routing_flags():
    text = EVENT_CLASSIFICATION.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_EVENT_CLASSIFICATION_PATTERNS:
        assert pattern not in text, (
            f"event_classification must not route on client/legacy flag: {pattern}"
        )


def test_progress_evaluator_no_supersede_api_hint():
    text = PROGRESS_EVALUATOR.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_PROGRESS_EVALUATOR_PATTERNS:
        assert pattern not in text, f"progress_evaluator legacy routing hint: {pattern}"


def test_session_controller_strips_client_hints():
    text = SESSION_CONTROLLER.read_text(encoding="utf-8")
    assert "merge_stripped_message_payload" in text
    assert "run_message_sync" in text


def test_control_payload_module_exists():
    from app.services.control_payload import (
        CLIENT_ROUTING_HINT_KEYS,
        strip_client_routing_hints,
    )

    payload = strip_client_routing_hints(
        {"goal": "x", "preempt": True, "replace_goal": True, "message": "x"}
    )
    assert "preempt" not in payload
    assert "replace_goal" not in payload
    assert payload["goal"] == "x"
    assert "preempt" in CLIENT_ROUTING_HINT_KEYS


def test_session_snapshot_module_exists():
    from app.runtime.agent_state_model import SessionSnapshot, validate_session_snapshot

    snap = validate_session_snapshot({"input_payload": {"fsm_state": "IDLE", "session_mode": "chat"}})
    assert isinstance(snap, SessionSnapshot)
    assert snap.fsm_state == "IDLE"


def test_routing_modules_do_not_use_taskstatus():
    for path in FORBIDDEN_ROUTING_TASKSTATUS_FILES:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_ROUTING_TASKSTATUS_PATTERNS:
            assert pattern not in text, f"{path.name} must not route on TaskStatus: {pattern}"


def test_client_payload_strips_legacy_replan_flags():
    from app.services.control_payload import strip_client_routing_hints

    payload = strip_client_routing_hints(
        {
            "goal": "x",
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "pending_replan": True,
        }
    )
    assert "require_planning_after_steer" not in payload
    assert payload["goal"] == "x"


def test_routing_needs_replan_reads_fsm_only():
    from app.runtime.state import create_initial_state, merge_state
    from app.services.session_fsm import routing_needs_replan

    idle = create_initial_state(input_payload={"goal": "x"})
    assert not routing_needs_replan(idle)
    replan = merge_state(
        idle,
        input_payload={
            "fsm_state": "REPLANNING",
            "require_planning_after_steer": True,
            "steer_planning_done": False,
        },
    )
    assert routing_needs_replan(replan)
