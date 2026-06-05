"""Legacy mission path hard-off and audit tests."""

from unittest.mock import patch

from app.config.settings import settings
from app.services.legacy_mission_paths import (
    LEGACY_MISSION_PATHS,
    block_legacy_writing_path,
    legacy_writing_path_allowed,
    load_legacy_manifest,
    record_legacy_mission_path,
)
from app.services.writing_phases import should_use_writing_llm_decide


def test_legacy_manifest_loads():
    paths = load_legacy_manifest()
    assert "writing_llm_decide" in paths


def test_legacy_paths_frozen_set():
    assert "writing_llm_decide" in LEGACY_MISSION_PATHS
    assert "manuscript_without_fact_bundle" in LEGACY_MISSION_PATHS


def test_legacy_writing_blocked_by_default():
    mission = {"kind": "writing", "objective": "novel", "orchestration": {"enabled": True}}
    with patch.object(settings, "MISSION_WRITING_LLM_DECIDE", True):
        with patch.object(settings, "MISSION_ALLOW_LEGACY_WRITING_PATH", False):
            with patch.object(settings, "MISSION_OMA_DEFAULT_FOR_WRITING", True):
                assert should_use_writing_llm_decide(mission) is False


def test_legacy_writing_allowed_when_flag_on():
    mission = {"kind": "writing", "objective": "novel", "orchestration": {"enabled": True}}
    with patch.object(settings, "MISSION_WRITING_LLM_DECIDE", True):
        with patch.object(settings, "MISSION_ALLOW_LEGACY_WRITING_PATH", True):
            with patch.object(settings, "MISSION_OMA_DEFAULT_FOR_WRITING", True):
                assert should_use_writing_llm_decide(mission) is True


def test_block_legacy_records_metric():
    with patch.object(settings, "MISSION_ALLOW_LEGACY_WRITING_PATH", False):
        assert block_legacy_writing_path("writing_llm_decide") is True
        assert legacy_writing_path_allowed() is False


def test_record_legacy_increments_counter():
    from app.services.metrics_service import get_metrics_service

    metrics = get_metrics_service()
    before = metrics._counters.get("legacy_mission:writing_llm_decide", 0)
    record_legacy_mission_path("writing_llm_decide")
    after = metrics._counters.get("legacy_mission:writing_llm_decide", 0)
    assert after >= before + 1
