from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    role: str
    content: str
    metadata: dict[str, Any] | None = None
