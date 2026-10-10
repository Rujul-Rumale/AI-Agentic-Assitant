"""Polling scheduler (spec Â§45).

Checks `monitoring` for rows whose `unread_check_at` or `read_unanswered_check_at`
has elapsed, dispatches them to the state machine, and loops. Runs in-process
via asyncio; no external daemon. Detaches per-coroutine to keep the loop
responsive to in-flight timer resets.
"""

from __future__ import annotations

import asyncio

from agent.logging_setup import get_logger
from agent.monitoring.state import MonitoringEngine

log = get_logger("agent.scheduler")

POLL_INTERVAL = 5  # seconds


class Scheduler:
    def __init__(self, engine: MonitoringEngine, interval: int = POLL_INTERVAL) -> None:
        self.engine = engine
        self.interval = interval
        self._task: asyncio.Task[None] | None = None  # type: ignore[type-arg]
        self._workers: set[asyncio.Task] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler")
        log.info("scheduler_started", interval=self.interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for worker in list(self._workers):
            worker.cancel()
        for worker in list(self._workers):
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        log.info("scheduler_stopped")

    def _spawn(self, name: str, coro) -> None:
        task = asyncio.create_task(coro, name=name)
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("scheduler_tick_error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def _tick(self) -> None:
        now = self._now()
        if self.engine.reminders is not None:
            self._spawn("reminders", self.engine.run_due_reminders())
        due_rows = await self.engine.db.fetch_all(
            "SELECT message_id, state, unread_check_at, read_unanswered_check_at "
            "FROM monitoring "
            "WHERE (unread_check_at IS NOT NULL AND unread_check_at <= ?) "
            "OR (read_unanswered_check_at IS NOT NULL AND read_unanswered_check_at <= ?) "
            "ORDER BY COALESCE(unread_check_at, read_unanswered_check_at) ASC "
            "LIMIT 20",
            (now, now),
        )
        for row in due_rows:
            mid = row["message_id"]
            has_unread = row["unread_check_at"] is not None and row["unread_check_at"] <= now
            has_read = (
                row["read_unanswered_check_at"] is not None
                and row["read_unanswered_check_at"] <= now
            )
            try:
                if has_unread:
                    self._spawn(f"unread-{mid[:8]}", self.engine.run_unread_due(mid))
                elif has_read:
                    self._spawn(f"read-{mid[:8]}", self.engine.run_read_unanswered_due(mid))
            except Exception:
                log.exception("scheduler_dispatch_error", message_id=mid)

    @staticmethod
    def _now() -> str:
        from agent.timeutil import ts_now

        return ts_now()