from app.services.artifact_content import parse_requested_chars


def test_parse_wan():
    assert parse_requested_chars("一次需要写够一万字") == 10000


def test_parse_digits():
    assert parse_requested_chars("写8000字") == 8000
