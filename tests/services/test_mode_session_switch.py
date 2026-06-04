from app.services.session.turn_policy import resolve_session_turn


def test_explicit_mission_payload_isolates_for_engineering_goal():
    decision = resolve_session_turn(
        {},
        {
            "goal": "做一个 2048 网页游戏，落盘到 games 目录",
            "mission": {"kind": "writing", "objective": "novel"},
        },
        "做一个 2048 网页游戏，落盘到 games 目录",
    )
    assert decision.intent == "isolate_qa"
    assert decision.source == "pattern_kind"


def test_manuscript_session_isolates_for_engineering_goal():
    state = {
        "mission": {"kind": "writing", "objective": "novel"},
        "manuscript": {"body_path": "novel.txt", "body_bytes": 5000},
    }
    decision = resolve_session_turn(
        state,
        {"goal": "做一个 2048 网页游戏"},
        "做一个 2048 网页游戏",
    )
    assert decision.intent == "isolate_qa"
    assert decision.kind in ("interactive_app", "code", "small_project", None) or decision.source in (
        "pattern_kind",
        "default_suspend",
    )
