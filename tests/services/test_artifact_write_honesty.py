from app.runtime.state import merge_state
from app.services.artifact_write_honesty import annotate_write_honesty, write_unchanged_after_read
from app.services.turn_contract import is_turn_contract_fulfilled, validate_turn_contract_execution


def test_write_unchanged_after_read_detects_no_op(base_state):
    state = merge_state(
        base_state,
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {
                    "filename": "a.txt",
                    "raw_content": "same text",
                    "total_chars": 9,
                    "status": "ok",
                },
            },
            {
                "tool": "write_text_artifact",
                "status": "ok",
                "result": {"filename": "a.txt", "bytes": 9, "status": "ok"},
            },
        ],
    )
    assert write_unchanged_after_read(state) is True


def test_write_changed_after_read_passes(base_state):
    state = merge_state(
        base_state,
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {
                    "filename": "a.txt",
                    "raw_content": "old",
                    "total_chars": 3,
                    "status": "ok",
                },
            },
            {
                "tool": "write_text_artifact",
                "status": "ok",
                "result": {"filename": "a.txt", "bytes": 12, "status": "ok"},
            },
        ],
    )
    assert write_unchanged_after_read(state) is False
    facts = annotate_write_honesty({}, list(state.get("tool_results") or []))
    assert facts.get("write_verified") is True


def test_validate_flags_unchanged_write_contract(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {
                "primary_op": "write_artifact",
                "tools": ["write_text_artifact"],
            },
        },
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "a.txt", "raw_content": "x" * 20, "total_chars": 20},
            },
            {
                "tool": "write_text_artifact",
                "status": "ok",
                "result": {"filename": "a.txt", "bytes": 20},
            },
        ],
        turn_facts={"write_verified": False},
    )
    assert "contract_write_unchanged" in validate_turn_contract_execution(state)
    assert not is_turn_contract_fulfilled(state)
