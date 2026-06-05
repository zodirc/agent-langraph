"""Client disconnect must not cancel the task."""

from app.services.graph_runner import _format_stream_exception


def test_generator_exit_message_does_not_imply_cancel():
    msg = _format_stream_exception(GeneratorExit())
    assert "not cancelled" in msg.lower()
    assert "disconnect" in msg.lower()
