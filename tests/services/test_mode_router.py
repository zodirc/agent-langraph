from app.runtime.state import merge_state
from app.services.mode_registry import get_mode_contract, load_mode_contracts
from app.services.mode_router import (
    infer_intent_kind,
    map_intent_to_mode,
    resolve_target_mode,
)
from app.services.mode_resolution import (
    apply_mode_contract_to_state,
    should_route_engineering_execution,
)
from app.services.project_verify.config import allowed_backend_ids
from app.services.project_verify.backends import resolve_project_backend_id
from app.services.route_audit.inference import infer_goal_kind_from_text


def test_intent_to_target_mode_mapping():
    assert map_intent_to_mode("interactive_app") == "engineering_mode"
    assert map_intent_to_mode("small_project") == "engineering_mode"
    assert map_intent_to_mode("code") == "engineering_mode"
    assert map_intent_to_mode("manuscript") == "manuscript_mode"
    assert map_intent_to_mode("qa") == "qa_mode"


def test_infer_interactive_app_from_goal():
    inf = infer_goal_kind_from_text("请做一个浏览器 2048 小游戏，要能直接打开")
    assert inf["primary_kind"] == "interactive_app"
    assert float(inf["confidence"]) > 0


def test_resolve_engineering_mode():
    state = {
        "task_id": "t-mode-1",
        "input_payload": {
            "goal": "生成 2048 网页游戏，落盘到 games 目录",
            "route_audit": {
                "inferred_kind": "interactive_app",
                "kind_confidence": 0.8,
            },
        },
    }
    resolution = resolve_target_mode(state)
    assert resolution.target_mode == "engineering_mode"
    assert resolution.intent_kind == "interactive_app"


def test_apply_contract_sets_engineering_tools():
    from app.runtime.state import create_initial_state

    state = create_initial_state(
        task_id="t-mode-2",
        input_payload={"goal": "cpp demo", "route_audit": {"inferred_kind": "code"}},
    )
    state = merge_state(state, selected_tools=["write_text_artifact"])
    resolution = resolve_target_mode(state)
    updated = apply_mode_contract_to_state(state, resolution)
    assert should_route_engineering_execution(updated)
    tools = updated.get("selected_tools") or []
    assert "write_file" in tools
    assert "write_text_artifact" not in tools
    intent = (updated.get("input_payload") or {}).get("writing_intent") or {}
    assert intent.get("enabled") is False


def test_mode_contracts_loaded():
    contracts = load_mode_contracts()
    assert "engineering_mode" in contracts
    eng = get_mode_contract("engineering_mode")
    assert eng is not None
    assert eng.execution.path == "engineering_bounded"
    assert "verify_backend" in eng.allowed_tools
    assert eng.security.shell_access is False


def test_backend_whitelist_and_selector():
    assert "web_html_js" in allowed_backend_ids()
    assert resolve_project_backend_id(intent_kind="interactive_app") == "web_html_js"
    assert resolve_project_backend_id(intent_kind="small_project") == "make_cpp_demo"
    assert resolve_project_backend_id(intent_kind="code", goal="用 C++ 实现") == "cpp"


def test_illegal_backend_not_in_whitelist():
    assert resolve_project_backend_id(intent_kind="unknown_kind") is None
