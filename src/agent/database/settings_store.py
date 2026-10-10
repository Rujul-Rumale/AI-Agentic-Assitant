"""Shared settings store helpers (pause/resume flags, config knobs)."""

from __future__ import annotations

from agent.database.db import Database
from agent.timeutil import ts_now


class SettingsStore:
    """Read/write key/value settings persisted in the `settings` table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, key: str, default: str = "false") -> str:
        row = await self.db.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return (await self.get(key, "true" if default else "false")).lower() in {"1", "true", "yes", "on"}

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, ts_now()),
        )

    async def set_bool(self, key: str, value: bool) -> None:
        await self.set(key, "true" if value else "false")

    async def is_monitoring_paused(self) -> bool:
        return await self.get_bool("monitoring.paused", False)

    async def is_ack_paused(self) -> bool:
        return await self.get_bool("acknowledgement.paused", False)