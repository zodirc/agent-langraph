from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.state_store import get_state_store


def test_save_preserves_pending_steer_when_act_snapshot_omits_it(isolated_stores):
    """Regression: mission_act save must not wipe queued steer from DB."""
    store = get_state_store()
    task_id = "steer-persist-1"
    base = merge_state(
        create_initial_state(task_id=task_id, input_payload={"goal": "写长篇"}),
        status=TaskStatus.MISSION_RUNNING.value,
    )
    store.save(base)
    store.save(
        merge_state(
            base,
            pending_user_message={"message": "按电视剧人物重写大纲", "queued_at": "t"},
        )
    )
    act_only = merge_state(
        base,
        status=TaskStatus.MISSION_RUNNING.value,
        current_node="mission_act",
    )
    store.save(act_only)
    loaded = store.load(task_id)
    assert loaded is not None
    pending = loaded.get("pending_user_message") or {}
    assert pending.get("message") == "按电视剧人物重写大纲"

    store.save(merge_state(loaded, pending_user_message=None))
    cleared = store.load(task_id)
    assert cleared is not None
    assert not cleared.get("pending_user_message")


def test_save_preserves_steer_planning_gate_in_payload(isolated_stores):
    """Regression: mission_act must not drop require_planning_after_steer from input_payload."""
    store = get_state_store()
    task_id = "steer-plan-gate-1"
    base = merge_state(
        create_initial_state(task_id=task_id, input_payload={"goal": "写长篇"}),
        status=TaskStatus.MISSION_RUNNING.value,
        steer_applied_at="2026-05-26T00:00:00Z",
    )
    store.save(base)
    store.save(
        merge_state(
            base,
            input_payload={
                **base["input_payload"],
                "require_planning_after_steer": True,
                "steer_planning_done": False,
            },
        )
    )
    act_only = merge_state(
        base,
        input_payload={"goal": "写长篇"},
        current_node="mission_act",
    )
    store.save(act_only)
    loaded = store.load(task_id)
    assert loaded is not None
    payload = loaded.get("input_payload") or {}
    assert payload.get("require_planning_after_steer") is True
    assert loaded.get("steer_applied_at") == "2026-05-26T00:00:00Z"


def test_new_steer_does_not_restore_stale_outcome_gate(isolated_stores):
    """After steer clears outcome gate, mission_act snapshot must not resurrect it."""
    store = get_state_store()
    task_id = "steer-outcome-clear-1"
    base = merge_state(
        create_initial_state(task_id=task_id, input_payload={"goal": "写长篇"}),
        status=TaskStatus.MISSION_RUNNING.value,
        steer_applied_at="2026-05-26T10:00:00Z",
    )
    store.save(base)
    store.save(
        merge_state(
            base,
            input_payload={
                **base["input_payload"],
                "steer_outcome_pending_confirm": True,
                "steer_outcome_confirmation": {"summary_text": "old"},
            },
        )
    )
    new_steer = merge_state(
        base,
        steer_applied_at="2026-05-26T11:00:00Z",
        input_payload={
            **base["input_payload"],
            "steer_outcome_pending_confirm": False,
            "goal": "写长篇",
        },
    )
    act_snapshot = merge_state(
        new_steer,
        steer_applied_at="2026-05-26T11:00:00Z",
        input_payload={"goal": "写长篇"},
        current_node="mission_act",
    )
    store.save(act_snapshot)
    loaded = store.load(task_id)
    assert loaded is not None
    payload = loaded.get("input_payload") or {}
    assert not payload.get("steer_outcome_pending_confirm")
    assert "steer_outcome_confirmation" not in payload


def test_save_returns_preserved_payload(isolated_stores):
    store = get_state_store()
    task_id = "save-return-1"
    base = merge_state(
        create_initial_state(task_id=task_id, input_payload={"goal": "x"}),
        status=TaskStatus.MISSION_RUNNING.value,
    )
    store.save(base)
    store.save(
        merge_state(
            base,
            input_payload={**base["input_payload"], "require_planning_after_steer": True},
        )
    )
    act_only = merge_state(base, input_payload={"goal": "x"}, current_node="mission_act")
    returned = store.save(act_only)
    assert (returned.get("input_payload") or {}).get("require_planning_after_steer") is True


def test_state_store_roundtrip(isolated_stores):
    store = get_state_store()
    state = create_initial_state(task_id="persist-1", input_payload={"goal": "x"})
    store.save(state)
    loaded = store.load("persist-1")
    assert loaded is not None
    assert loaded["task_id"] == "persist-1"
    assert loaded["input_payload"]["goal"] == "x"
