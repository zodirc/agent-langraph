from app.runtime.state import create_initial_state
from app.services.writing_turn_footer import append_writing_footer, record_writing_turn_metadata


def test_append_writing_footer_to_answer():
    state = create_initial_state(task_id="footer-1")
    record_writing_turn_metadata(
        state,
        material_usage_line="本轮素材使用：素材卡 2,310 字",
        chapter_shortfall="本章 1200 字，低于目标 3000 字。",
    )
    answer = append_writing_footer("正文已写好。", state)
    assert "本轮素材使用" in answer
    assert "低于目标 3000 字" in answer
