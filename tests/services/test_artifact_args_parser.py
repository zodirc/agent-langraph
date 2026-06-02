"""Artifact args incremental parser."""

from app.services.artifact_args_parser import ArtifactArgsParser, extract_content_so_far


def test_extract_content_partial_unclosed_string():
    raw = '{"content": "第一章'
    assert extract_content_so_far(raw) == "第一章"


def test_extract_content_with_escapes():
    raw = r'{"content": "行1\n行2"'
    assert extract_content_so_far(raw) == "行1\n行2"


def test_parser_incremental_feed():
    parser = ArtifactArgsParser()
    for piece in ['{"con', 'tent": "你', '好', '世界"']:
        parser.feed(piece)
    assert parser.content_so_far() == "你好世界"


def test_discouraged_prefix_before_content():
    parser = ArtifactArgsParser()
    parser.feed('{"reasoning": "long analysis", "content": "正文')
    assert parser.discouraged_prefix_detected()
    assert parser.content_so_far() == "正文"


def test_stall_preview_truncates():
    parser = ArtifactArgsParser()
    parser.feed("x" * 500)
    preview = parser.stall_preview(80)
    assert len(preview) <= 82
    assert preview.endswith("…")
