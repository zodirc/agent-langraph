from app.services.writing_quality import (
    quality_score_use_llm,
    score_chapter_quality,
    score_chapter_quality_heuristic,
)


def test_quality_score_use_llm_respects_model_disabled(monkeypatch):
    import app.config.settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "WRITING_QUALITY_SCORE_USE_LLM", True)
    monkeypatch.setattr(settings_mod.settings, "MODEL_ENABLED", False)
    assert quality_score_use_llm() is False


def test_score_chapter_quality_defaults_to_heuristic_when_llm_off(monkeypatch):
    import app.config.settings as settings_mod
    import app.services.writing_quality as wq

    monkeypatch.setattr(settings_mod.settings, "WRITING_QUALITY_SCORE_USE_LLM", False)
    monkeypatch.setattr(settings_mod.settings, "MODEL_ENABLED", True)

    text = "第一章开始了。" * 40
    rubric = score_chapter_quality(chapter_text=text, outline_slice="主角出场")
    expected = score_chapter_quality_heuristic(
        chapter_text=text, outline_slice="主角出场"
    )
    assert rubric.continuity_score == expected.continuity_score
    assert rubric.outline_alignment == expected.outline_alignment


def test_score_chapter_quality_uses_llm_when_enabled(monkeypatch):
    import app.config.settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "WRITING_QUALITY_SCORE_USE_LLM", True)
    monkeypatch.setattr(settings_mod.settings, "MODEL_ENABLED", True)

    def fake_invoke(purpose, system, user, **kwargs):
        return {
            "continuity_score": 0.81,
            "outline_alignment": 0.77,
            "character_consistency": 0.8,
            "duplication_risk": 0.1,
            "chapter_completion": 0.9,
            "hook_quality": 0.85,
            "notes": "节奏尚可",
        }

    monkeypatch.setattr(
        "app.services.llm_client.invoke_structured",
        fake_invoke,
    )
    rubric = score_chapter_quality(
        chapter_text="上一章结尾。本章继续推进冲突。" * 30,
        prev_chapter_text="前文铺垫。" * 20,
        outline_slice="本章：对峙升级",
    )
    assert rubric.continuity_score == 0.81
    assert rubric.outline_alignment == 0.77
