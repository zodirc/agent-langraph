from datetime import datetime, timedelta, timezone

from app.runtime.state import TaskStatus, merge_state
from app.services.review_timeout_service import ReviewTimeoutService
from app.services.state_store import get_state_store


def test_review_timeout_auto_reject(base_state, isolated_stores, test_settings, monkeypatch):
    import app.services.review_timeout_service as rts_mod
    import app.config.settings as settings_module

    monkeypatch.setattr(settings_module.settings, "REVIEW_TIMEOUT_ENABLED", True)
    monkeypatch.setattr(settings_module.settings, "REVIEW_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(settings_module.settings, "REVIEW_TIMEOUT_ACTION", "reject")
    monkeypatch.setattr(rts_mod, "settings", settings_module.settings)

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state = merge_state(
        base_state,
        status=TaskStatus.WAITING_REVIEW.value,
        review_required=True,
        review_requested_at=past,
    )
    get_state_store().save(state)

    service = ReviewTimeoutService()
    count = service.process_expired()
    assert count == 1
    updated = get_state_store().load(state["task_id"])
    assert updated is not None
    assert updated["status"] == TaskStatus.ABANDONED.value
