"""Writing generation lifecycle."""

from app.services.writing_generation import begin_writing_generation, map_close_status


def test_map_close_status():
    assert map_close_status(has_content=True, stream_interrupted=False) == "ok"
    assert map_close_status(has_content=True, stream_interrupted=True) == "partial"
    assert map_close_status(has_content=False, stream_interrupted=True) == "failed"
    assert map_close_status(has_content=False, stream_interrupted=False, aborted=True) == "aborted"


def test_generation_meta():
    rec = begin_writing_generation(task_id="t1", filename="novel.txt", segment_index=1)
    rec.finish(status="partial", args_bytes=1000, content_bytes=200, error_class="server_error")
    meta = rec.to_meta()
    assert meta["generation_id"] == rec.generation_id
    assert meta["generation_status"] == "partial"
    assert meta["segment_index"] == 1
    assert meta["writing_contract_version"] == "wgc/1"
