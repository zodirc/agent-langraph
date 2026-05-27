"""
Domain pack parameter versions learned from user feedback (Ch9).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings


class PackParamsStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pack_feedback_params (
                    pack_name TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    params_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pack_params_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    params_json TEXT NOT NULL,
                    experiment_tag TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get_params(self, pack_name: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT params_json, version FROM pack_feedback_params WHERE pack_name = ?",
                (pack_name.lower(),),
            ).fetchone()
        if not row:
            return {}
        data = json.loads(row["params_json"])
        if isinstance(data, dict):
            data["_version"] = int(row["version"])
        return data if isinstance(data, dict) else {}

    def save_version_snapshot(
        self,
        pack_name: str,
        params: dict[str, Any],
        *,
        experiment_tag: str = "",
    ) -> None:
        version = int(params.get("_version", 0))
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pack_params_history (pack_name, version, params_json, experiment_tag, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pack_name.lower(),
                    version,
                    json.dumps({k: v for k, v in params.items() if not str(k).startswith("_")}),
                    experiment_tag,
                    now,
                ),
            )
            conn.commit()

    def list_versions(self, pack_name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT version, params_json, experiment_tag, created_at
                FROM pack_params_history
                WHERE pack_name = ?
                ORDER BY version DESC
                LIMIT ?
                """,
                (pack_name.lower(), limit),
            ).fetchall()
        return [
            {
                "version": int(r["version"]),
                "params": json.loads(r["params_json"]),
                "experiment_tag": r["experiment_tag"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def bump_from_feedback(
        self,
        pack_name: str,
        *,
        rating: int,
        outcome: str,
        experiment_tag: str = "",
    ) -> dict[str, Any]:
        """Adjust pack defaults slightly from positive feedback."""
        current = self.get_params(pack_name)
        version = int(current.pop("_version", 0)) + 1
        params = dict(current)
        if rating >= 4 or outcome == "success":
            params["memory_retrieval_boost"] = min(
                0.5, float(params.get("memory_retrieval_boost", 0)) + 0.05
            )
            params["prefer_tools"] = bool(params.get("prefer_tools", True))
        elif rating <= 2 or outcome == "failure":
            params["memory_retrieval_boost"] = max(
                0.0, float(params.get("memory_retrieval_boost", 0)) - 0.02
            )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pack_feedback_params (pack_name, version, params_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pack_name) DO UPDATE SET
                    version = excluded.version,
                    params_json = excluded.params_json,
                    updated_at = excluded.updated_at
                """,
                (pack_name.lower(), version, json.dumps(params), now),
            )
            conn.commit()
        params["_version"] = version
        self.save_version_snapshot(pack_name, params, experiment_tag=experiment_tag)
        return params


_store: PackParamsStore | None = None


def get_pack_params_store() -> PackParamsStore:
    global _store
    if _store is None:
        _store = PackParamsStore()
    return _store
