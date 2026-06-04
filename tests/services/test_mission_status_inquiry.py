from app.services.interaction_goal import goal_is_mission_status_query
from app.services.mission_steer import steer_needs_planning_llm


def test_goal_is_mission_status_query():
    assert goal_is_mission_status_query("你正在做什么")
    assert goal_is_mission_status_query("  当前进度？  ")
    assert not goal_is_mission_status_query("继续写下一章")


def test_steer_status_query_skips_planning_llm():
    assert steer_needs_planning_llm(message="你正在做什么") is False
