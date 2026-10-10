"""Timer math (spec §15-16). All times are tz-aware UTC datetimes."""

from __future__ import annotations

from datetime import timedelta

from agent.timeutil import from_ts, now_utc, to_ts


def unread_check_time(received_at: str, threshold_minutes: int) -> str:
    """Deadline after which an unread message should be triaged."""
    base = from_ts(received_at) or now_utc()
    return to_ts(base + timedelta(minutes=threshold_minutes))


def read_unanswered_check_time(read_at: str, threshold_minutes: int) -> str:
    """Deadline after which a read-but-unanswered message triggers a reminder."""
    base = from_ts(read_at) or now_utc()
    return to_ts(base + timedelta(minutes=threshold_minutes))


def minutes_between(start: str, end_offset: str | None = None) -> int:
    started = from_ts(start)
    if started is None:
        return 0
    end = from_ts(end_offset) if end_offset else now_utc()
    return int((end - started).total_seconds() // 60)