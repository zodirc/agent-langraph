"""CI gate: legacy mission manifest must stay in sync with code constants."""

from app.services.legacy_mission_paths import LEGACY_MISSION_PATHS, load_legacy_manifest


def test_legacy_manifest_matches_code_constants():
    manifest = set(load_legacy_manifest())
    assert LEGACY_MISSION_PATHS.issubset(manifest)


def test_legacy_manifest_no_unknown_entries():
    allowed = set(LEGACY_MISSION_PATHS)
    for path in load_legacy_manifest():
        assert path in allowed, f"unknown legacy path in manifest: {path}"
