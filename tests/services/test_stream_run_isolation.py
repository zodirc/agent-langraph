"""SSE side-channel isolation across superseded graph runs."""

from app.runtime.state import TaskStatus, merge_state
from app.services.graph_runner import (
    _accept_stream_side_event,
    _drain_writing_queue,
)
from app.services.mission_handoff import complete_mission_handoff
from app.services.mission_schema import build_mission_dict
from app.services.stream_progress import (
    clear_stream_run_context,
    report_writing_delta,
    set_stream_run_context,
    set_writing_handler,
)


def test_accept_stream_side_event_filters_stale_run():
    assert _accept_stream_side_event(
        {"run_id": "old", "foreground_epoch": 2},
        run_id="new",
        foreground_epoch=3,
    ) is False
    assert _accept_stream_side_event(
        {"run_id": "new", "foreground_epoch": 3},
        run_id="new",
        foreground_epoch=3,
    ) is True
    assert _accept_stream_side_event(
        {"run_id": "new", "foreground_epoch": 2},
        run_id="new",
        foreground_epoch=3,
    ) is False


def test_drain_writing_queue_skips_superseded_run_events():
    events: list[str] = []
    for chunk in _drain_writing_queue(
        "t1",
        _queue_with(
            {"run_id": "stale", "node": "writing", "phase": "x", "text": "old", "filename": "a.txt"},
            {"run_id": "active", "node": "writing", "phase": "x", "text": "new", "filename": "a.txt"},
        ),
        run_id="active",
        foreground_epoch=2,
    ):
        events.append(chunk)
    assert len(events) == 1
    assert "new" in events[0]


def test_report_writing_delta_tags_thread_local_run():
    captured: list[dict] = []

    def _capture(item: dict) -> None:
        captured.append(item)

    set_writing_handler(_capture)
    set_stream_run_context(run_id="run-abc", foreground_epoch=4)
    try:
        report_writing_delta(node="writing", phase="artifact", text="hello", filename="x.txt")
    finally:
        clear_stream_run_context()
        set_writing_handler(None)

    assert captured[0]["run_id"] == "run-abc"
    assert captured[0]["foreground_epoch"] == 4


def test_handoff_preserves_edit_plot_contract(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    payload = {
        "goal": "改剧情",
        "latest_steer_message": "使用原电影人物",
        "turn_contract": {
            "primary_op": "edit_plot",
            "tools": ["read_text_artifact", "edit_text_artifact"],
            "forbid": ["write_body", "append_body"],
        },
        "writing_intent": {"enabled": False, "action": "edit_plot"},
        "mission": mission,
    }
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=2,
        status=TaskStatus.PLANNED.value,
        input_payload=payload,
    )
    handed = complete_mission_handoff(state, payload)
    out = handed.get("input_payload") or {}
    assert (out.get("turn_contract") or {}).get("primary_op") == "edit_plot"
    intent = out.get("writing_intent") or {}
    assert intent.get("action") == "edit_plot"
    assert intent.get("enabled") is False


def _queue_with(*items):
    import queue

    q: queue.SimpleQueue[dict] = queue.SimpleQueue()
    for item in items:
        q.put(item)
    return q
