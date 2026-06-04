"""Deterministic minimal repair before LLM repair (§9.4)."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.artifact_tools import task_artifact_dir
from app.services.session_fs_tools import handle_write_file


def _session_root(task_id: str) -> Path:
    return task_artifact_dir(task_id).resolve()


def minimal_repair_files(
    task_id: str,
    paths: list[str],
    *,
    stderr: str,
    intent_kind: str,
) -> bool:
    """
    Apply small deterministic fixes from compiler/linter stderr.
    Returns True if any file was modified.
    """
    err = (stderr or "").lower()
    if not err.strip():
        return False
    root = _session_root(task_id)
    changed = False
    for rel in paths:
        path = (root / rel).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = content
        if intent_kind == "code" or rel.endswith((".cpp", ".cc", ".py")):
            if "expected" in err and "}" in err and content.count("{") > content.count("}"):
                updated = content.rstrip() + "\n}\n"
            if "expected ';'" in err or "expected ;" in err:
                if not updated.rstrip().endswith(";"):
                    updated = updated.rstrip() + ";\n"
        if intent_kind == "interactive_app" or rel.endswith(".js"):
            if "syntaxerror" in err or "unexpected token" in err:
                if updated.strip() and not updated.rstrip().endswith(";"):
                    updated = updated.rstrip() + ";\n"
        if updated != content:
            handle_write_file(
                {"task_id": task_id, "path": rel, "content": updated, "parents": True}
            )
            changed = True
    return changed
