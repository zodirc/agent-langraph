"""Tests for message-centric persistence (Phase 2+3)."""

from __future__ import annotations

import uuid

from app.runtime.state import create_initial_state
from app.services.chat_message_service import (
    ChatMessageService,
    StreamMessagePersistCtx,
    get_chat_message_service,
)
from app.services.chat_message_store import (
    MESSAGE_STATUS_COMPLETED,
    MESSAGE_STATUS_STREAMING,
    get_chat_message_store,
)
from app.services.message_projection import (
    apply_event_to_blocks,
    ensure_answer_in_blocks,
    finalize_blocks,
    is_telemetry_event,
    project_events_to_blocks,
    sort_blocks_for_display,
)


def test_projection_thinking_and_answer():
    blocks: list = []
    blocks = apply_event_to_blocks(blocks, event_type="thinking_delta", delta="think", meta={})
    blocks = apply_event_to_blocks(blocks, event_type="answer_delta", delta="Hi", meta={})
    blocks = finalize_blocks(blocks)
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["text"] == "think"
    assert blocks[1]["type"] == "answer"
    assert blocks[1]["text"] == "Hi"
    assert blocks[0]["status"] == "completed"


def test_chat_message_roundtrip(isolated_stores):
    task_id = f"t-msg-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    assert isinstance(svc, ChatMessageService)
    svc._store = store
    state = create_initial_state(task_id=task_id, session_id=task_id)
    state["session_turn"] = 1

    user_id = svc.record_user_message(state, text="Hello", client_message_id="c1")
    assert user_id

    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        user_message_id=user_id,
        next_seq=store.next_event_seq(task_id),
    )
    svc.record_stream_event(ctx, event_type="thinking_delta", delta="hmm", meta={})
    assert ctx.assistant_message_id
    svc.record_stream_event(ctx, event_type="answer_delta", delta="World", meta={})
    svc.finalize_assistant_message(ctx, status=MESSAGE_STATUS_COMPLETED)

    view = svc.build_session_view(task_id)
    assert view["message_count"] == 2
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == MESSAGE_STATUS_COMPLETED
    assert assistant["content"] == "World"
    assert any(b.get("type") == "thinking" for b in assistant["structured_blocks"])

    events = store.list_events(task_id, after_seq=0)
    assert len(events) == 2
    assert len({e["message_id"] for e in events}) == 1
    assert events[0]["seq"] == 1


def test_finalize_syncs_content_into_answer_block():
    blocks = apply_event_to_blocks([], event_type="thinking_delta", delta="hmm", meta={})
    blocks = finalize_blocks(blocks)
    synced = ensure_answer_in_blocks(blocks, "Final answer body")
    answer = next(b for b in synced if b["type"] == "answer")
    assert answer["text"] == "Final answer body"
    assert answer["status"] == "completed"


def test_finalize_assistant_message_syncs_final_answer(isolated_stores):
    task_id = f"t-final-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        next_seq=store.next_event_seq(task_id),
    )
    svc.record_stream_event(ctx, event_type="thinking_delta", delta="think", meta={})
    svc.finalize_assistant_message(
        ctx,
        status=MESSAGE_STATUS_COMPLETED,
        content="Delivered final answer",
    )
    assistant = store.list_messages(task_id)[0]
    assert assistant["content"] == "Delivered final answer"
    answer = next(b for b in assistant["structured_blocks"] if b["type"] == "answer")
    assert answer["text"] == "Delivered final answer"


def test_telemetry_events_excluded_from_blocks():
    blocks = apply_event_to_blocks([], event_type="ui_progress", delta="working", meta={"phase": "working"})
    assert blocks == []
    assert is_telemetry_event("ui_node")
    assert not is_telemetry_event("trace")


def test_sort_blocks_for_display():
    blocks = [
        {"type": "answer", "text": "hi"},
        {"type": "thinking", "text": "hmm"},
        {"type": "trace", "text": "log"},
    ]
    sorted_blocks = sort_blocks_for_display(blocks)
    assert [b["type"] for b in sorted_blocks] == ["thinking", "trace", "answer"]


def test_build_session_view_rebuilds_blocks_from_events(isolated_stores):
    task_id = f"t-rebuild-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        next_seq=store.next_event_seq(task_id),
    )
    for chunk in ("think", "-part", "-two", "-three"):
        svc.record_stream_event(ctx, event_type="thinking_delta", delta=chunk, meta={})
    svc.record_stream_event(ctx, event_type="answer_delta", delta="Answer", meta={})
    # Simulate stale materialized view (partial flush) on disk.
    assert ctx.assistant_message_id
    store.update_message(
        ctx.assistant_message_id,
        structured_blocks=[{"type": "thinking", "text": "think", "status": "streaming"}],
        content="",
    )
    svc.finalize_assistant_message(ctx, status=MESSAGE_STATUS_COMPLETED, content="Answer")

    view = svc.build_session_view(task_id)
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    thinking = next(b for b in assistant["structured_blocks"] if b["type"] == "thinking")
    assert thinking["text"] == "think-part-two-three"
    answer = next(b for b in assistant["structured_blocks"] if b["type"] == "answer")
    assert answer["text"] == "Answer"


def test_project_many_thinking_deltas():
    blocks: list = []
    for i in range(50):
        blocks = apply_event_to_blocks(
            blocks, event_type="thinking_delta", delta=f"t{i}", meta={}
        )
    blocks = finalize_blocks(blocks)
    assert blocks[0]["text"] == "".join(f"t{i}" for i in range(50))


def test_thinking_snapshot_overwrites_partial_deltas():
    blocks: list = []
    blocks = apply_event_to_blocks(blocks, event_type="thinking_delta", delta="The", meta={})
    blocks = apply_event_to_blocks(
        blocks,
        event_type="thinking_snapshot",
        delta="The user asked a question. Full reasoning here.",
        meta={"snapshot": True},
    )
    thinking = next(b for b in blocks if b["type"] == "thinking")
    assert thinking["text"] == "The user asked a question. Full reasoning here."


def test_sync_thinking_text_persists_snapshot(isolated_stores):
    task_id = f"t-think-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        next_seq=store.next_event_seq(task_id),
    )
    svc.record_stream_event(ctx, event_type="thinking_delta", delta="The", meta={})
    svc.sync_thinking_text(
        ctx,
        "The user asked a question. Full reasoning here.",
        persist_snapshot_event=True,
    )
    view = svc.build_session_view(task_id)
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    thinking = next(b for b in assistant["structured_blocks"] if b["type"] == "thinking")
    assert "Full reasoning here" in thinking["text"]
    events = store.list_events(task_id, after_seq=0)
    assert any(e["event_type"] == "thinking_snapshot" for e in events)


def test_telemetry_roundtrip(isolated_stores):
    task_id = f"t-tel-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        next_seq=store.next_event_seq(task_id),
    )
    svc.record_stream_event(
        ctx,
        event_type="ui_task_created",
        meta={"task_id": task_id, "session_id": task_id, "continued": False},
    )
    svc.record_stream_event(ctx, event_type="ui_progress", delta="started", meta={"phase": "started"})
    svc.record_stream_event(ctx, event_type="thinking_delta", delta="think", meta={})
    svc.finalize_assistant_message(ctx, status=MESSAGE_STATUS_COMPLETED, content="Hi")

    view = svc.build_session_view(task_id)
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    assert not any(b.get("type", "").startswith("ui_") for b in assistant["structured_blocks"])
    events = store.list_events(task_id, after_seq=0)
    assert any(e["event_type"] == "ui_progress" for e in events)


def test_build_session_view_repairs_stale_streaming(isolated_stores):
    task_id = f"t-stale-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    ctx = StreamMessagePersistCtx(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        next_seq=store.next_event_seq(task_id),
    )
    svc.record_stream_event(ctx, event_type="thinking_delta", delta="The user said hi", meta={})
    svc.record_stream_event(ctx, event_type="answer_delta", delta="你好", meta={})
    assert ctx.assistant_message_id
    store.update_message(ctx.assistant_message_id, status=MESSAGE_STATUS_STREAMING)

    state = create_initial_state(task_id=task_id, session_id=task_id)
    state["status"] = "COMPLETED"
    state["final_answer"] = "你好！很高兴见到你。"

    view = svc.build_session_view(task_id, task_state=state)
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == MESSAGE_STATUS_COMPLETED
    assert assistant["content"] == "你好！很高兴见到你。"
    assert view["streaming_answer_active"] is False
    answer = next(b for b in assistant["structured_blocks"] if b["type"] == "answer")
    assert "你好" in answer["text"]


def test_streaming_assistant_status(isolated_stores):
    task_id = f"t2-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store
    row = store.create_message(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        role="assistant",
        status=MESSAGE_STATUS_STREAMING,
        content="partial",
    )
    view = svc.build_session_view(task_id)
    assert view["streaming_answer_active"] is True
    assert view["streaming_message_id"] == row["message_id"]


def test_build_session_view_multi_turn_thinking_isolated(isolated_stores):
    task_id = f"t-multi-{uuid.uuid4().hex[:8]}"
    store = get_chat_message_store()
    svc = get_chat_message_service()
    svc._store = store

    turn1 = store.create_message(
        task_id=task_id,
        session_id=task_id,
        session_turn=1,
        role="assistant",
        status=MESSAGE_STATUS_COMPLETED,
        content="Turn one answer",
    )
    store.append_event(
        message_id=turn1["message_id"],
        task_id=task_id,
        seq=1,
        event_type="thinking_delta",
        delta="turn1-think",
        meta={},
    )
    store.append_event(
        message_id=turn1["message_id"],
        task_id=task_id,
        seq=2,
        event_type="answer_delta",
        delta="Turn one answer",
        meta={},
    )

    turn2 = store.create_message(
        task_id=task_id,
        session_id=task_id,
        session_turn=2,
        role="assistant",
        status=MESSAGE_STATUS_STREAMING,
        content="",
    )
    store.append_event(
        message_id=turn2["message_id"],
        task_id=task_id,
        seq=3,
        event_type="thinking_delta",
        delta="turn2-think",
        meta={},
    )

    state = create_initial_state(task_id=task_id, session_id=task_id)
    state["session_turn"] = 2
    state["streaming_thinking_text"] = "turn2-live-draft"
    state["status"] = "PLANNED"

    view = svc.build_session_view(task_id, task_state=state)
    assistants = [m for m in view["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 2
    first, second = assistants[0], assistants[1]
    first_thinking = next(b for b in first["structured_blocks"] if b["type"] == "thinking")
    assert first_thinking["text"] == "turn1-think"
    assert "turn2-live-draft" not in first_thinking["text"]
    assert second.get("streaming_thinking_text") == "turn2-live-draft"
    assert view["streaming_message_id"] == turn2["message_id"]
