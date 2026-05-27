from app.services.stream_progress import (
    report_answer_delta,
    report_thinking_delta,
    set_answer_handler,
    set_thinking_handler,
)


def test_report_thinking_delta():
    events: list[dict] = []
    set_thinking_handler(lambda p: events.append(p))
    try:
        report_thinking_delta(node="reasoning", phase="llm", text="CoT ")
        assert events[0]["text"] == "CoT "
    finally:
        set_thinking_handler(None)


def test_report_answer_delta():
    events: list[dict] = []
    set_answer_handler(lambda p: events.append(p))
    try:
        report_answer_delta(node="reasoning", phase="llm", text="答", field="summary")
        assert events[0]["field"] == "summary"
    finally:
        set_answer_handler(None)
