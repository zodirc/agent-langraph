from app.runtime.state import create_initial_state, merge_state
from app.services.live_task_state import LiveTaskEntry
from app.services.state_debug_view import build_task_state_debug_response


def test_debug_response_merges_store_and_live():
    store = merge_state(
        create_initial_state(input_payload={"goal": "a"}),
        status="MISSION_RUNNING",
        progress={"work_plan": [{"id": "1", "status": "pending"}]},
    )
    live = merge_state(
        store,
        progress={"work_plan": [{"id": "1", "status": "running"}]},
        current_node="writing",
    )
    live_entry = LiveTaskEntry(state=live, updated_at="2026-01-01T00:00:00+00:00", running=True)
    resp = build_task_state_debug_response(
        task_id=store["task_id"],
        store_state=store,
        live_entry=live_entry,
        truncate=False,
    )
    assert resp["live_available"] is True
    assert resp["live_running"] is True
    assert resp["sources"]["store"] is not None
    assert resp["sources"]["live"] is not None
    merged_plan = resp["sources"]["merged"]["state"]["progress"]["work_plan"]
    assert merged_plan[0]["status"] == "running"
