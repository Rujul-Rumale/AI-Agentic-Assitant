"""Self-chat reminders & scheduled messages (spec extension).

Users can create reminders from self-chat ("remind me to X in 30 minutes")
or via ``/remind``. A scheduler tick fires due reminders back into the
self-chat conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from agent.timeutil import from_ts, now_utc, to_ts, ts_now

if TYPE_CHECKING:
    from agent.database.db import Database

REMINDER_ID_PREFIX = "REMIND"


@dataclass
class Reminder:
    reminder_id: str
    chat_id: str
    kind: str
    text: str
    due_at: str
    status: str
    created_at: str
    fired_at: str | None = None


def next_reminder_id(existing: list[str]) -> str:
    """Monotonic `REMIND-00001` style id based on existing ids."""
    highest = 0
    for rid in existing:
        m = re.search(r"(\d+)$", rid or "")
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{REMINDER_ID_PREFIX}-{highest + 1:05d}"


def parse_remind_request(
    text: str,
    *,
    timezone: str = "Asia/Kolkata",
    now: datetime | None = None,
) -> tuple[str, datetime, str] | None:
    """Parse ``remind me to X in N minutes``-style text.

    Returns ``(task, due_dt_utc_aware, kind)`` or None. ``kind`` is REMINDER
    for short-term pops and SCHEDULED_MESSAGE for tomorrow/at-time items.
    """
    if not text or len(text.strip()) < 6:
        return None

    low = text.strip().lower()

    # "remind me to <task> in <n> <unit>"
    m = re.match(
        r"^\s*remind\s+me\s+to\s+(.+?)\s+in\s+(\d+)\s*"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|h|m|s)\b(.+)?$",
        low,
    )
    if m:
        task = (m.group(1) + (m.group(4) or "")).strip()
        unit = m.group(3).lower()
        seconds = _unit_seconds(unit)
        due = (now or now_utc()) + timedelta(seconds=int(m.group(2)) * seconds)
        return task, due, "REMINDER"

    # "remind me to <task> tomorrow"
    m = re.match(r"^\s*remind\s+me\s+to\s+(.+?)\s+tomorrow\b(.+)?$", low)
    if m:
        task = (m.group(1) + (m.group(2) or "")).strip()
        tz = ZoneInfo(timezone)
        local = (now or now_utc()).astimezone(tz)
        target = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return task, target.astimezone(UTC), "SCHEDULED_MESSAGE"

    # "remind me to <task> at HH:MM [am|pm]" (also HH[am|pm])
    m = re.match(
        r"^\s*remind\s+me\s+to\s+(.+?)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(.+)?$",
        low,
    )
    if m:
        task = (m.group(1) + (m.group(5) or "")).strip()
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        ampm = (m.group(4) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        tz = ZoneInfo(timezone)
        local = (now or now_utc()).astimezone(tz)
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        return task, target.astimezone(UTC), "SCHEDULED_MESSAGE"

    return None


def _unit_seconds(unit: str) -> int:
    u = unit.lower()
    if u.startswith(("s", "sec")):
        return 1
    if u.startswith(("m", "min")):
        return 60
    return 3600


def parse_remind_command(args: tuple[str, ...], *, timezone: str = "Asia/Kolkata") -> tuple[str, datetime, str] | None:
    """Parse ``/remind 30m/2h/tomorrow/at 5pm <text>`` style command args."""
    if not args:
        return None
    joined = " ".join(args)
    # leading duration: "<n>m|min|h|hr <text>"
    m = re.match(r"^(\d+)\s*(m|min|mins|h|hr|hrs)\s+(.+)$", joined, re.IGNORECASE)
    if m:
        unit = m.group(2).lower()
        seconds = 60 if unit.startswith("m") else 3600
        due = now_utc() + timedelta(seconds=int(m.group(1)) * seconds)
        return m.group(3).strip(), due, "REMINDER"
    # leading "tomorrow"/"today": delegate to NL parser on "<token> <text>"
    m = re.match(r"^(tomorrow|today|at)\s+(.+)$", joined, re.IGNORECASE)
    if m:
        return parse_remind_request(
            f"remind me to {m.group(2)} {m.group(1)}", timezone=timezone
        )
    return parse_remind_request("remind me to " + joined, timezone=timezone)


class ReminderStore:
    def __init__(self, db: Database, *, self_chat_id: str = "") -> None:
        self.db = db
        self.self_chat_id = self_chat_id

    async def create(self, text: str, due_at: datetime, *, kind: str = "REMINDER", chat_id: str | None = None) -> Reminder:
        existing = await self.db.fetch_all("SELECT reminder_id FROM reminders")
        rid = next_reminder_id([r["reminder_id"] for r in existing])
        created = ts_now()
        await self.db.execute(
            "INSERT INTO reminders (reminder_id, chat_id, kind, text, due_at, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, chat_id or self.self_chat_id, kind, text, to_ts(due_at), "PENDING", created),
        )
        return Reminder(rid, chat_id or self.self_chat_id, kind, text, to_ts(due_at), "PENDING", created)

    async def list_pending(self, limit: int = 20) -> list[Reminder]:
        rows = await self.db.fetch_all(
            "SELECT * FROM reminders WHERE status = 'PENDING' ORDER BY due_at ASC LIMIT ?",
            (limit,),
        )
        return [_row_to_reminder(r) for r in rows]

    async def cancel(self, reminder_id: str) -> Reminder | None:
        row = await self.db.fetch_one(
            "SELECT * FROM reminders WHERE reminder_id = ? AND status = 'PENDING'",
            (reminder_id,),
        )
        if row is None:
            return None
        await self.db.execute(
            "UPDATE reminders SET status = 'CANCELLED', updated_at = ? "
            "WHERE reminder_id = ? AND status = 'PENDING'",
            (ts_now(), reminder_id),
        )
        return _row_to_reminder(row)

    async def fire_due(self, *, now: datetime | None = None) -> list[Reminder]:
        """Return and mark all due PENDING reminders (idempotent resilience)."""
        cutoff = to_ts(now or now_utc())
        rows = await self.db.fetch_all(
            "SELECT * FROM reminders WHERE status = 'PENDING' AND due_at <= ? ORDER BY due_at ASC",
            (cutoff,),
        )
        fired = [_row_to_reminder(r) for r in rows]
        stamp = ts_now()
        for r in fired:
            await self.db.execute(
                "UPDATE reminders SET status = 'FIRED', updated_at = ?, fired_at = ? "
                "WHERE reminder_id = ? AND status = 'PENDING'",
                (stamp, stamp, r.reminder_id),
            )
            r.status = "FIRED"
            r.fired_at = stamp
        return fired

    async def count_pending(self) -> int:
        row = await self.db.fetch_one("SELECT COUNT(*) c FROM reminders WHERE status = 'PENDING'")
        return int(row["c"]) if row else 0


def _row_to_reminder(row: dict) -> Reminder:
    from agent.database.db import Database  # noqa: F401  (row already a dict)

    return Reminder(
        reminder_id=row["reminder_id"],
        chat_id=row["chat_id"],
        kind=row["kind"],
        text=row["text"],
        due_at=row["due_at"],
        status=row["status"],
        created_at=row["created_at"],
        fired_at=row.get("fired_at"),
    )


def format_reminders(reminders: list[Reminder]) -> str:
    if not reminders:
        return "No pending reminders."
    from agent.timeutil import from_ts

    lines = ["Pending reminders:"]
    for r in reminders:
        due = from_ts(r.due_at)
        when = due.strftime("%d %b %H:%M") if due else r.due_at
        lines.append(f"{r.reminder_id} — {r.text} (due {when})")
    return "\n".join(lines)