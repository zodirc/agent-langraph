from app.services.retrieval_policy import (
    needs_session_memory_retrieval,
    retrieval_domains_for_state,
    restrict_memory_to_current_session,
    should_route_to_retrieval_after_planning,
    should_skip_session_memory_retrieval,
)


def test_needs_session_memory_when_multi_turn():
    state = {
        "session_turn": 3,
        "input_payload": {"conversation_history": []},
    }
    assert needs_session_memory_retrieval(state) is True


def test_restrict_memory_on_new_session_first_turn():
    state = {
        "session_turn": 1,
        "input_payload": {
            "new_session": True,
            "goal": "写一篇小说",
            "conversation_history": [{"role": "user", "content": "写一篇小说"}],
        },
    }
    assert restrict_memory_to_current_session(state) is True


def test_restrict_memory_false_on_continue_goal():
    state = {
        "session_turn": 1,
        "input_payload": {"goal": "继续之前的写作", "conversation_history": []},
    }
    assert restrict_memory_to_current_session(state) is False


def test_restrict_memory_true_when_goal_has_mission_phase_tags_only():
    state = {
        "session_turn": 1,
        "input_payload": {
            "goal": "写一篇小说 [append_body] 写第1章",
            "conversation_history": [{"role": "user", "content": "写一篇小说"}],
        },
    }
    assert restrict_memory_to_current_session(state) is True


def test_skip_session_memory_on_autonomous_writing_first_turn():
    state = {
        "session_turn": 1,
        "mission": {"kind": "writing"},
        "input_payload": {
            "mission": {"kind": "writing"},
            "writing_intent": {"enabled": True, "action": "append_body"},
            "conversation_history": [{"role": "user", "content": "写小说"}],
        },
    }
    assert should_skip_session_memory_retrieval(state) is True


def test_skip_session_memory_false_when_user_second_turn():
    state = {
        "session_turn": 2,
        "mission": {"kind": "writing"},
        "input_payload": {
            "writing_intent": {"enabled": True, "action": "append_body"},
            "conversation_history": [
                {"role": "user", "content": "写小说"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "改冷峻一点"},
            ],
        },
    }
    assert should_skip_session_memory_retrieval(state) is False


def test_restrict_memory_false_after_turn_two():
    state = {"session_turn": 2, "input_payload": {"conversation_history": []}}
    assert restrict_memory_to_current_session(state) is False


def test_should_route_when_skip_retrieval_but_session_memory_needed():
    state = {
        "skip_retrieval": True,
        "session_turn": 2,
        "input_payload": {},
    }
    assert should_route_to_retrieval_after_planning(state) is True


def test_retrieval_domains_for_writing_mission():
    state = {"mission": {"kind": "writing"}, "input_payload": {}}
    assert retrieval_domains_for_state(state) == {"writing", "common"}


def test_retrieval_domains_for_code_task():
    state = {"task_type": "code", "input_payload": {}}
    assert retrieval_domains_for_state(state) == {"code", "common"}


def test_retrieval_domains_default_common():
    state = {"task_type": "qa", "input_payload": {}}
    assert retrieval_domains_for_state(state) == {"common"}
