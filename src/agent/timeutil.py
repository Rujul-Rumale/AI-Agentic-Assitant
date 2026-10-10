"""Timezone-aware time helpers. UTC is used internally for all scheduling."""

from __future__ import annotations

from datetime import UTC, datetime

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def now_utc() -> datetime:
    """Current time as a tz-aware UTC datetime."""
    return datetime.now(UTC)


def ts_now() -> str:
    """ISO-8601 (UTC, tz-aware) string for storage."""
    return now_utc().isoformat().replace("+00:00", "Z")


def to_ts(dt: datetime) -> str:
    """Serialize a tz-aware datetime into the canonical storage string."""
    dt = dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def from_ts(value: str | None) -> datetime | None:
    """Parse a stored timestamp into a tz-aware datetime, or None."""
    if not value:
        return None
    if isinstance(value, str) and value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def utc_display(value: str | None) -> str | None:
    """Human display (UTC) form for a stored timestamp."""
    parsed = from_ts(value)
    return parsed.strftime("%H:%M") if parsed else value