from app.domain.writing_memory_models import StoryBible, StoryBibleEntry
from app.runtime.state import merge_state
from app.services.manuscript_context import build_writing_context
from app.services.outline_body_alignment import decide_outline_body_alignment
from app.services.outline_diff import compute_outline_diff_heuristic
from app.services.writing_quality import score_chapter_quality_heuristic


def test_build_writing_context_has_l2_l3(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art
    from app.services.writing_memory import save_story_bible

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = base_state["task_id"]
    art_dir = art.task_artifact_dir(task_id)
    (art_dir / "novel.txt").write_text(
        "### 第1章 开端\n\n张三来到青石镇。\n\n（第1章完）",
        encoding="utf-8",
    )
    (art_dir / "outline.txt").write_text(
        "第1章 开端\n张三入镇\n\n第2章 冲突\n张三遇见李四",
        encoding="utf-8",
    )

    bible = StoryBible()
    bible.entries["char:张三"] = StoryBibleEntry(
        key="char:张三",
        category="character",
        display_name="张三",
        content="主角，谨慎冷静",
        activation_keywords=["张三", "青石镇"],
        priority=900,
    )
    save_story_bible(task_id, bible)

    state = merge_state(
        base_state,
        manuscript={"body_path": "novel.txt", "outline_path": "outline.txt"},
        input_payload={
            **base_state["input_payload"],
            "novel_filename": "novel.txt",
            "outline_filename": "outline.txt",
        },
    )
    ctx = build_writing_context(
        task_id=task_id,
        state=state,
        body_filename="novel.txt",
        outline_filename="outline.txt",
        chapter_index=2,
    )

    assert "memory_tier" in ctx
    assert "prev_chapter_summary" in ctx
    assert "current_chapter_goal" in ctx
    assert isinstance(ctx.get("story_bible_entries") or [], list)
    guidelines = ctx.get("writing_guidelines_excerpt")
    if guidelines:
        assert "RAG" in (ctx.get("memory_tier") or {})


def test_outline_alignment_supports_multilevel_actions():
    old_outline = "第1章 开端\n主角入城\n\n第2章 追查\n寻找线索"
    new_outline = "第1章 开端\n主角入城\n\n第2章 转折\n新增密探线\n\n第3章 追查\n推进线索"
    diff = compute_outline_diff_heuristic(old_outline, new_outline)
    decision = decide_outline_body_alignment(
        outline_before_excerpt=old_outline,
        outline_after_excerpt=new_outline,
        body_tail_excerpt="主角在夜里收到匿名信",
        body_total_chars=3200,
        last_chapter_index=2,
        outline_diff=diff,
    )
    assert decision.body_action in {
        "keep_append",
        "append_with_bridge",
        "patch_recent_chapters",
        "rewrite_body",
    }
    assert isinstance(decision.affected_chapters, list)


def test_quality_heuristic_outputs_gate_fields():
    rubric = score_chapter_quality_heuristic(
        chapter_text="张三推门而入，发现案卷被人动过。窗外雷声滚滚，他意识到真正的追杀才刚开始。",
        prev_chapter_text="张三抵达青石镇，住进客栈。",
        outline_slice="第2章 冲突\n张三发现关键案卷被偷，决定冒险追查。",
        story_bible_excerpt={"characters": {"张三": {"summary": "主角"}}},
    )
    data = rubric.to_dict()
    assert 0 <= data["composite_score"] <= 1
    assert "pass_gate" in data
