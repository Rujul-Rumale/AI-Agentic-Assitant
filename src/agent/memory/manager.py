"""Persistent, selective memory (spec §51-52).

Not every WhatsApp message is stored as memory — only explicit structured facts
(people, projects, preferences, commitments) written via `remember`. The store
is a simple `key = (entity, attribute)` KV in SQLite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.database.db import Database


class MemoryManager:
    def __init__(self, db: Database, enabled: bool = False) -> None:
        self.db = db
        self.enabled = enabled

    async def remember(self, entity: str, attribute: str, value: str) -> None:
        if not self.enabled:
            return
        await self.db.execute(
            "INSERT INTO memory (entity, attribute, value, created_at, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(entity, attribute) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (entity, attribute, value, ts_now(), ts_now()),
        )

    async def recall(self, entity: str, attribute: str | None = None) -> list[str]:
        if not self.enabled:
            return []
        if attribute:
            rows = await self.db.fetch_all(
                "SELECT value FROM memory WHERE entity = ? AND attribute = ?", (entity, attribute)
            )
        else:
            rows = await self.db.fetch_all("SELECT value FROM memory WHERE entity = ? ORDER BY updated_at DESC", (entity,))
        return [r["value"] for r in rows]

    async def recall_for(self, sender_id: str, chat_id: str) -> list[str]:
        """Relevant memory for context resolution (spec §52)."""
        notes: list[str] = []
        notes.extend(await self.recall("contact:" + sender_id))
        notes.extend(await self.recall("chat:" + chat_id))
        return notes