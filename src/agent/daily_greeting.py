"""Daily good-morning self-chat status message (scheduled in agent.main)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.logging_setup import get_logger
from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.database.db import Database
    from agent.database.settings_store import SettingsStore

log = get_logger("agent.daily_greeting")

RETRY_BACKOFF_SECONDS = 60


def build_greeting_text(local_date: str, counts: dict[str, int], health_lines: list[str]) -> str:
    """Compose the plain-WhatsApp greeting text (unit-testable, no side effects)."""
    lines = ["Good morning!"]
    if health_lines:
        lines.append("")
        lines.extend(health_lines)
    if counts:
        lines.append("")
        lines.append(f"Status {local_date}:")
        lines.extend(f"{key}: {value}" for key, value in counts.items())
    lines.append("")
    lines.append("All systems normal.")
    return "\n".join(lines)


async def _counts(db: Database) -> dict[str, int]:
    async def _count(sql: str) -> int:
        row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {sql}")
        return int(row["n"]) if row else 0

    return {
        "messages": await _count("messages"),
        "open tasks": await _count(
            "tasks WHERE status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS')"
        ),
        "events": await _count("events"),
    }


def _load_tz(name: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("daily_greeting_tz_fallback", tz=name)
        return timezone(timedelta(hours=5, minutes=30))


def _next_target(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


async def run_daily_greeting(parts: dict[str, Any], hour: int = 5, minute: int = 0) -> None:
    """Send one good-morning self-chat status per local day, then wait for the next slot."""
    settings = parts["settings"]
    self_chat_id = settings.whatsapp.self_chat_id
    if not self_chat_id:
        log.warning("daily_greeting_disabled", reason="no self_chat_id configured")
        return

    db: Database = parts["db"]
    store: SettingsStore = parts["store"]
    engine = parts["engine"]
    tz = _load_tz(settings.timezone)

    while True:
        now = datetime.now(tz)
        local_date = now.strftime("%Y-%m-%d")
        key = f"daily_greeting:{local_date}"

        if await store.get(key, default=None) is None:
            counts = await _counts(db)
            model = settings.llm.model or settings.llm.provider
            health_lines = [
                f"Agent: {settings.agent.name} - running",
                f"LLM: {model}",
                f"Bridge: {settings.whatsapp.bridge_url}",
            ]
            msg = build_greeting_text(local_date, counts, health_lines)
            try:
                await engine._send(chat_id=self_chat_id, text=msg, force=True)
            except Exception as exc:
                log.warning(
                    "daily_greeting_send_failed", chat_id=self_chat_id, error=str(exc)[:200]
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            await store.set(key, ts_now())
            await engine.audit("DAILY_GREETING_SENT", chat_id=self_chat_id, date=local_date)
            log.info("daily_greeting_sent", chat_id=self_chat_id, date=local_date)

        target = _next_target(datetime.now(tz), hour, minute)
        await asyncio.sleep((target - datetime.now(tz)).total_seconds())