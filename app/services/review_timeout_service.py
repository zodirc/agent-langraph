from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.config.settings import settings
from app.runtime.state import TaskStatus, append_audit, merge_state
from app.services.metrics_service import get_metrics_service
from app.services.state_store import get_state_store

logger = logging.getLogger(__name__)


class ReviewTimeoutService:
    """Expire WAITING_REVIEW tasks per architecture §22.1 SLA."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not settings.REVIEW_TIMEOUT_ENABLED:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="review-timeout")
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(settings.REVIEW_TIMEOUT_POLL_SECONDS):
            try:
                self.process_expired()
            except Exception as exc:
                logger.exception("Review timeout check failed: %s", exc)

    def process_expired(self) -> int:
        store = get_state_store()
        expired = store.list_waiting_review_older_than(settings.REVIEW_TIMEOUT_MINUTES)
        count = 0
        for state in expired:
            action = settings.REVIEW_TIMEOUT_ACTION.lower()
            if action == "escalate":
                updated = merge_state(
                    state,
                    policy_result="REVIEW",
                    review_required=True,
                    audit_log=append_audit(state, "review_timeout", "escalated"),
                )
            else:
                updated = merge_state(
                    state,
                    policy_result="REJECT",
                    review_required=False,
                    status=TaskStatus.ABANDONED.value,
                    current_node="review_timeout",
                    final_answer="Task auto-rejected: human review SLA exceeded.",
                    audit_log=append_audit(
                        state,
                        "review_timeout",
                        "auto_reject",
                        {"timeout_minutes": settings.REVIEW_TIMEOUT_MINUTES},
                    ),
                )
                get_metrics_service().inc_policy_reject()
            store.save(updated)
            count += 1
            logger.info("Review timeout applied to task %s", state["task_id"])
        return count

    @staticmethod
    def is_expired(review_requested_at: str | None) -> bool:
        if not review_requested_at:
            return False
        requested = datetime.fromisoformat(review_requested_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_minutes = (now - requested).total_seconds() / 60.0
        return delta_minutes >= settings.REVIEW_TIMEOUT_MINUTES


_service: ReviewTimeoutService | None = None


def get_review_timeout_service() -> ReviewTimeoutService:
    global _service
    if _service is None:
        _service = ReviewTimeoutService()
    return _service
