"""Render skill overlay templates with skill_params (e.g. {{goal}})."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def render_skill_text(text: str, params: dict[str, Any] | None) -> str:
    if not text or not params:
        return text or ""
    out = text

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in params:
            return str(params[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_repl, out)
