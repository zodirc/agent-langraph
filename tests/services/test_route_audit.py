from app.runtime.state import merge_state
from app.services.route_audit.apply import apply_route_corrections, writing_gate_allowed
from app.services.route_audit.audit import audit_planned_route, detect_planned_route
from app.services.route_audit.inference import infer_task_kind
from app.services.route_audit.pipeline import run_route_audit_pipeline


def _base_state(**overrides):
    state = {
        "task_id": "t-route-audit",
        "session_id": "s-route-audit",
        "session_turn": 13,
        "skip_retrieval": True,
        "input_payload": {
            "goal": "你重新试试之前未成功的操作：用 C++ 链表实现大数相加",
            "conversation_history": [
                {"role": "user", "content": "用 C++ 写大数相加"},
            ],
            "session_outcomes": [
                {"turn": 11, "outcome": "rejected", "reason": "output_guard pii"},
            ],
        },
        "selected_tools": ["echo", "write_text_artifact"],
        "plan": ["write code to artifact"],
    }
    state.update(overrides)
    return state


def test_infer_task_kind_code_or_retry():
    state = _base_state()
    result = infer_task_kind(state)
    assert result["primary_kind"] in ("code", "retry_recovery")
    assert float(result["confidence"]) > 0


def test_audit_blocks_writing_artifact_for_code_goal():
    state = merge_state(
        _base_state(),
        input_payload={
            **_base_state()["input_payload"],
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
    )
    audit = audit_planned_route(state)
    assert audit["planned_route"] == "writing_artifact"
    assert audit["aligned"] is False
    assert "disable_writing_intent" in audit["corrections"]


def test_apply_corrections_disables_writing_gate():
    state = merge_state(
        _base_state(),
        input_payload={
            **_base_state()["input_payload"],
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
    )
    audit = audit_planned_route(state)
    updated = apply_route_corrections(state, audit)
    assert writing_gate_allowed(updated) is False
    assert (updated.get("input_payload") or {}).get("force_slow_reasoning") is True
    assert "write_text_artifact" not in (updated.get("selected_tools") or [])


def test_pipeline_stores_route_audit_on_payload():
    state = merge_state(
        _base_state(),
        input_payload={
            **_base_state()["input_payload"],
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
    )
    updated = run_route_audit_pipeline(state)
    audit = (updated.get("input_payload") or {}).get("route_audit") or {}
    assert audit.get("writing_blocked") is True or audit.get("prior_issues")
    assert writing_gate_allowed(updated) is False


def test_code_tools_only_rewrite_keeps_write_tool():
    state = merge_state(
        _base_state(),
        input_payload={
            **_base_state()["input_payload"],
            "writing_intent": {"enabled": False},
            "tool_params": {
                "write_text_artifact": {"filename": "novel.txt", "task_id": "t-route-audit"},
            },
        },
        selected_tools=["write_text_artifact"],
    )
    audit = audit_planned_route(state)
    assert audit["planned_route"] == "writing_tools_only"
    if audit.get("aligned"):
        return
    updated = apply_route_corrections(state, audit)
    assert "write_text_artifact" in (updated.get("selected_tools") or [])
    wt = (updated.get("input_payload") or {})["tool_params"]["write_text_artifact"]
    assert str(wt["filename"]).endswith(".cpp")


def test_detect_planned_route_code_filename():
    state = merge_state(
        _base_state(),
        input_payload={
            **_base_state()["input_payload"],
            "writing_intent": {"enabled": True, "action": "write_body"},
            "tool_params": {
                "write_text_artifact": {"filename": "bignum.cpp", "task_id": "t-route-audit"},
            },
        },
    )
    assert detect_planned_route(state) == "writing_code_artifact"


def _write_manuscript_artifact(monkeypatch, tmp_path, task_id: str) -> None:
    from tests.conftest import patch_task_artifact_dir

    patch_task_artifact_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.services.artifact_resolver.task_artifact_dir",
        lambda tid: tmp_path / tid,
    )
    target = tmp_path / task_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "novel.txt").write_text("正文" * 6000, encoding="utf-8")


def test_qa_with_existing_manuscript_allows_writing_sub_intent(monkeypatch, tmp_path):
    task_id = "t-route-audit-mixed"
    _write_manuscript_artifact(monkeypatch, tmp_path, task_id)
    state = {
        "task_id": task_id,
        "session_id": "s-route-audit-mixed",
        "input_payload": {
            "goal": "你觉得写的怎么样，顺便帮我润色重写",
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
        "selected_tools": [],
        "plan": ["rewrite manuscript body"],
        "skip_retrieval": True,
    }
    audit = audit_planned_route(state)
    assert audit["planned_route"] == "writing_artifact"
    assert audit["inferred_kind"] == "manuscript"
    assert audit["aligned"] is True
    assert audit["writing_blocked"] is False


def test_retry_recovery_with_manuscript_rewrite_auto_switches(monkeypatch, tmp_path):
    task_id = "t-route-audit-retry-write"
    _write_manuscript_artifact(monkeypatch, tmp_path, task_id)
    state = {
        "task_id": task_id,
        "session_id": "s-route-audit-retry-write",
        "input_payload": {
            "goal": "重新试试，继续润色并重写这篇小说正文",
            "writing_intent": {"enabled": True, "action": "write_body"},
            "session_outcomes": [
                {"turn": 2, "outcome": "failed", "reason": "route conflict"},
            ],
        },
        "selected_tools": [],
        "plan": ["rewrite manuscript body"],
        "skip_retrieval": True,
    }
    inferred = infer_task_kind(state)
    assert inferred["primary_kind"] == "manuscript"
    assert inferred.get("switched_from") == "retry_recovery"
    audit = audit_planned_route(state)
    assert audit["planned_route"] == "writing_artifact"
    assert audit["inferred_kind"] == "manuscript"
    assert audit["aligned"] is True
