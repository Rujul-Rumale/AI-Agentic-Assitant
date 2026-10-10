"""Lightweight SQL migration runner.

Migrations are ordered `.sql` files under `migrations/`. Each is applied at
most once, tracked in `schema_migrations`. This keeps the DB bootstrap trivial
and dependency-free (spec §38).
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.database.db import Database
from agent.logging_setup import get_logger
from agent.timeutil import ts_now

log = get_logger("agent.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _migration_files() -> list[Path]:
    files = MIGRATIONS_DIR.glob("*.sql")
    return sorted(files, key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))


async def migrate(db: Database, migrations_dir: str | None = None) -> None:
    """Apply any pending migrations; idempotent across restarts."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    applied = {
        row["version"]
        for row in await db.fetch_all("SELECT version FROM schema_migrations")
    }

    directory = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
    for path in sorted(directory.glob("*.sql"), key=lambda p: int(re.match(r"(\d+)", p.name).group(1))):
        version = int(re.match(r"(\d+)", path.name).group(1))
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, path.name, ts_now()),
        )
        log.info("migration_applied", version=version, name=path.name)