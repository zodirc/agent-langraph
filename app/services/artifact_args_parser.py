"""Incremental extraction of ``content`` from streaming tool-call JSON args."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CONTENT_KEY_RE = re.compile(r'"content"\s*:\s*"', re.DOTALL)

# Keys that should not appear before content in tool args (contract hint).
_DISCOURAGED_PREFIX_KEYS = ("reasoning", "thinking", "analysis", "draft_notes")


@dataclass
class ArtifactArgsParser:
    """
    Stateful parser for partial ``submit_artifact`` JSON args.

    Prefer :meth:`content_so_far` over regex-only parsing for long streams where
    the ``content`` string is not yet closed.
    """

    accumulated: str = ""
    _content_key_seen: bool = field(default=False, init=False, repr=False)
    _content_start: int = field(default=-1, init=False, repr=False)

    def feed(self, piece: str) -> None:
        if piece:
            self.accumulated += piece
            if not self._content_key_seen:
                match = _CONTENT_KEY_RE.search(self.accumulated)
                if match:
                    self._content_key_seen = True
                    self._content_start = match.end()

    def has_content_key(self) -> bool:
        if self._content_key_seen:
            return True
        match = _CONTENT_KEY_RE.search(self.accumulated)
        if match:
            self._content_key_seen = True
            self._content_start = match.end()
            return True
        return False

    def content_so_far(self) -> str:
        """Decoded ``content`` field value (complete or partial open string)."""
        if not self.has_content_key():
            return ""
        return _decode_json_string_partial(self.accumulated, self._content_start)

    def args_len(self) -> int:
        return len(self.accumulated)

    def content_len(self) -> int:
        return len(self.content_so_far())

    def stall_preview(self, max_chars: int = 200) -> str:
        """Short diagnostic prefix for traces (newlines collapsed)."""
        raw = self.accumulated[:max_chars]
        one_line = " ".join(raw.split())
        if len(self.accumulated) > max_chars:
            return f"{one_line}…"
        return one_line

    def discouraged_prefix_detected(self) -> bool:
        lower = self.accumulated[:4096].lower()
        if not self.has_content_key():
            return any(f'"{key}"' in lower for key in _DISCOURAGED_PREFIX_KEYS)
        head = self.accumulated[: self._content_start].lower()
        return any(f'"{key}"' in head for key in _DISCOURAGED_PREFIX_KEYS)


def extract_content_so_far(accumulated: str) -> str:
    """One-shot extraction (stateless)."""
    parser = ArtifactArgsParser()
    parser.feed(accumulated)
    return parser.content_so_far()


def _decode_json_string_partial(raw: str, start: int) -> str:
    """Parse JSON string from ``start``; tolerate missing closing quote."""
    out: list[str] = []
    i = start
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == '"':
            break
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "b":
                out.append("\b")
            elif nxt == "f":
                out.append("\f")
            elif nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    i += 5
                except ValueError:
                    out.append(nxt)
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)
