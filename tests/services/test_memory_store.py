from app.domain.memory import MemoryRecord
from app.services.memory_store import get_memory_store


def test_memory_store_write_and_search(isolated_stores):
    store = get_memory_store()
    store.write(
        MemoryRecord(
            memory_id="m-1",
            task_id="t-1",
            user_id="u-1",
            task_type="qa",
            summary="LangGraph memory writeback works",
            tags=["langgraph"],
        )
    )
    hits = store.search("LangGraph memory", user_id="u-1")
    assert len(hits) >= 1
    assert hits[0]["summary"].startswith("LangGraph")
    assert hits[0]["semantic_score"] >= 0


def test_memory_store_hybrid_search_prefers_same_user(isolated_stores):
    store = get_memory_store()
    store.write(
        MemoryRecord(
            memory_id="m-2",
            task_id="t-2",
            user_id="u-1",
            task_type="writing",
            summary="Write chapter outline for a long novel about LangGraph agents",
            tags=["novel", "langgraph", "outline"],
            payload={"chapter": 1},
        )
    )
    store.write(
        MemoryRecord(
            memory_id="m-3",
            task_id="t-3",
            user_id="u-2",
            task_type="writing",
            summary="Unrelated memory for another user",
            tags=["other"],
        )
    )

    hits = store.search("long novel chapter outline", user_id="u-1")
    assert hits
    assert hits[0]["task_id"] == "t-2"
    assert hits[0]["memory_type"] == "episode"


def test_memory_store_create_session_summary(isolated_stores):
    store = get_memory_store()
    record = store.create_session_summary(
        task_id="task-1",
        user_id="u-1",
        task_type="qa",
        summary="user: 解释架构 | assistant: 已解释",
        conversation_history=[
            {"role": "user", "content": "解释架构"},
            {"role": "assistant", "content": "已解释"},
        ],
    )
    hits = store.search("解释架构", user_id="u-1")
    assert record.task_id == "task-1"
    assert any(hit["memory_type"] == "session_summary" for hit in hits)
