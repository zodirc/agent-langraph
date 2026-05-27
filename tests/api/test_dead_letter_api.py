from fastapi.testclient import TestClient

from app.main import app
from app.nodes.dead_letter_node import dead_letter_node
from app.runtime.state import TaskStatus, merge_state
from app.services.state_store import get_state_store


def test_list_dead_letter(isolated_stores):
    client = TestClient(app)
    from app.runtime.state import create_initial_state

    state = create_initial_state(task_id="dlq-api-test")
    get_state_store().save(state)
    dead_letter_node(
        merge_state(
            state,
            retry_count=3,
            status=TaskStatus.TOOL_FAILED.value,
            errors=["x"],
        )
    )
    response = client.get("/dead-letter")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
