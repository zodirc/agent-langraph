"""Read-only outline inspection route for session turns."""

from app.services.mission_steer import goal_requests_outline_read


def test_goal_requests_outline_read_positive():
    assert goal_requests_outline_read("我需要查看你重写后的大纲") is True
    assert goal_requests_outline_read("检阅 outline.txt") is True


def test_goal_requests_outline_read_negative():
    assert goal_requests_outline_read("继续写第5章正文") is False
    assert goal_requests_outline_read("") is False
