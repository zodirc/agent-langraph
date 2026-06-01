from app.services.task_agenda import (
    ensure_agenda_fields,
    items_from_plan_steps,
    propagate_failure,
    runnable_items,
    select_next_runnable_item,
    set_item_status,
)


def test_runnable_respects_dependencies():
    plan = ensure_agenda_fields(
        {
            "items": [
                {"id": "a", "kind": "step", "status": "pending", "depends_on": []},
                {"id": "b", "kind": "step", "status": "pending", "depends_on": ["a"]},
            ]
        }
    )
    ready = runnable_items(plan)
    assert len(ready) == 1
    assert ready[0]["id"] == "a"


def test_select_next_after_dependency_done():
    plan = ensure_agenda_fields(
        {
            "items": [
                {"id": "a", "kind": "step", "status": "done", "depends_on": []},
                {"id": "b", "kind": "step", "status": "pending", "depends_on": ["a"]},
            ]
        }
    )
    nxt = select_next_runnable_item(plan)
    assert nxt is not None
    assert nxt["id"] == "b"


def test_propagate_failure_blocks_dependents():
    plan = ensure_agenda_fields(
        {
            "items": [
                {"id": "a", "kind": "step", "status": "failed", "depends_on": []},
                {"id": "b", "kind": "step", "status": "pending", "depends_on": ["a"]},
            ]
        }
    )
    plan = propagate_failure(plan, "a")
    blocked = next(i for i in plan["items"] if i["id"] == "b")
    assert blocked["status"] == "blocked"


def test_items_from_plan_steps_chain():
    items = items_from_plan_steps(["step one", "step two"])
    assert len(items) == 2
    assert items[0]["depends_on"] == []
    assert items[1]["depends_on"] == [items[0]["id"]]


def test_set_item_status():
    plan = ensure_agenda_fields({"items": [{"id": "x", "status": "pending"}]})
    plan = set_item_status(plan, "x", "running", reason="test")
    assert plan["items"][0]["status"] == "running"
