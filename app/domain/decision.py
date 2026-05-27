from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PolicyDecision:
    result: str
    reason: str
    risk_level: str
    metadata: Optional[dict[str, Any]] = None
