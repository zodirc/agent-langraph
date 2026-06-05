from app.services.task_drift import detect_task_drift


def test_detect_task_drift_explicit_shift():
    state = {
        "input_payload": {"goal": "Instead let's talk about Docker deployment"},
        "conversation_history": [
            {"role": "user", "content": "Explain Python sorting algorithms"},
        ],
    }
    result = detect_task_drift(state)
    assert result["drifted"] is True
    assert result["reason"] == "explicit_topic_shift"


def test_no_drift_same_topic():
    state = {
        "input_payload": {"goal": "Tell me more about Python sorting"},
        "conversation_history": [
            {"role": "user", "content": "Explain Python sorting algorithms"},
        ],
    }
    assert detect_task_drift(state)["drifted"] is False
