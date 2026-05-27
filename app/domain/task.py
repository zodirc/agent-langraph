from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    user_id: str
    task_type: str
    status: str
    current_node: str
    input_payload: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    final_answer: Optional[str] = None
    structured_output: Optional[dict[str, Any]] = None
    artifacts: Optional[list[dict[str, Any]]] = None
