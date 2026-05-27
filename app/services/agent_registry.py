"""Agent registration and discovery for cross-service A2A (Ch15)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.domain.agent_message import AgentCard

logger = logging.getLogger(__name__)

_MEMORY: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


class AgentRegistry:
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
                CREATE TABLE IF NOT EXISTS agent_registry (
                    agent_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    card_json TEXT NOT NULL,
                    last_heartbeat TEXT NOT NULL,
                    ttl_sec INTEGER NOT NULL DEFAULT 300
                )
                """
            )
            conn.commit()

    def register(self, card: AgentCard, url: str, *, ttl_sec: int | None = None) -> None:
        ttl = ttl_sec if ttl_sec is not None else settings.A2A_REGISTRY_TTL_SEC
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "agent_id": card.agent_id,
            "url": url.rstrip("/"),
            "card": card.to_dict(),
            "last_heartbeat": now,
            "ttl_sec": ttl,
        }
        with _LOCK:
            _MEMORY[card.agent_id] = payload
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_registry (agent_id, url, card_json, last_heartbeat, ttl_sec)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    url = excluded.url,
                    card_json = excluded.card_json,
                    last_heartbeat = excluded.last_heartbeat,
                    ttl_sec = excluded.ttl_sec
                """,
                (card.agent_id, payload["url"], json.dumps(payload["card"]), now, ttl),
            )
            conn.commit()

    def heartbeat(self, agent_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE agent_registry SET last_heartbeat = ? WHERE agent_id = ?",
                (now, agent_id),
            )
            conn.commit()
            if cur.rowcount:
                with _LOCK:
                    if agent_id in _MEMORY:
                        _MEMORY[agent_id]["last_heartbeat"] = now
                return True
        return False

    def discover(self, capability: str) -> list[tuple[AgentCard, str]]:
        cap = capability.lower().strip()
        results: list[tuple[AgentCard, str]] = []
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            rows = conn.execute("SELECT agent_id, url, card_json, last_heartbeat, ttl_sec FROM agent_registry").fetchall()
        for row in rows:
            hb = datetime.fromisoformat(str(row["last_heartbeat"]).replace("Z", "+00:00"))
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            ttl = int(row["ttl_sec"] or settings.A2A_REGISTRY_TTL_SEC)
            if now - hb > timedelta(seconds=ttl):
                continue
            card_data = json.loads(row["card_json"])
            card = AgentCard.from_dict(card_data)
            caps = [c.lower() for c in card.capabilities]
            if cap in caps or cap in [d.lower() for d in card.domains]:
                results.append((card, str(row["url"])))
        return results

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id, url, card_json, last_heartbeat, ttl_sec FROM agent_registry"
            ).fetchall()
        return [
            {
                "agent_id": r["agent_id"],
                "url": r["url"],
                "card": json.loads(r["card_json"]),
                "last_heartbeat": r["last_heartbeat"],
                "ttl_sec": r["ttl_sec"],
            }
            for r in rows
        ]


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
