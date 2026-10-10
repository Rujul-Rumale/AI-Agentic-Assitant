"""Audit event logging (spec §42, §61). Fast, fire-and-forget, never blocks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.database.db import Database


async def audit(
    db: Database,
    event_type: str,
    *,
    chat_id: str | None = None,
    message_id: str | None = None,
    task_id: str | None = None,
    details: Any = None,
    result: str | None = None,
    **extra: Any,
) -> None:
    """Append one row to the `events` table. Extra kwargs fold into `details`."""
    if extra:
        base = dict(details) if isinstance(details, dict) else {"details": details} if details is not None else {}
        base.update(extra)
        details = base
    await db.execute(
        "INSERT INTO events (timestamp, event_type, chat_id, message_id, task_id, details, result) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            ts_now(),
            event_type,
            chat_id,
            message_id,
            task_id,
            json.dumps(details) if details is not None else None,
            result,
        ),
    )