"""Task manager (spec §31, §43): creation with dedupe, resolution, dismissal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.database.models import Task, make_task
from agent.logging_setup import get_logger
from agent.tasks.models import TaskStatus, next_task_id
from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.database.db import Database
    from agent.database.models import Message
    from agent.llm.schemas import Classification

log = get_logger("agent.tasks")


class TaskManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, message: Message, classification: Classification) -> Task | None:
        """Create a task for an actionable message, or return the open one.

        Dedupe rules (spec §43):
        - one open task per `source_message_id`;
        - an open task for the same chat within 10 minutes is consolidated
          (message grouping) instead of creating a second obligation.
        """
        existing = await self._open_for_source(message.message_id)
        if existing:
            return existing

        recent = await self._open_for_chat_recent(message.chat_id, minutes=10)
        if recent:
            await self.db.execute(
                "UPDATE tasks SET summary = ?, requested_action = ?, urgency = ?, updated_at = ? WHERE task_id = ?",
                (classification.summary, classification.requested_action, classification.urgency.value, ts_now(), recent),
            )
            task = await self.get(recent)
            log.info("task_consolidated", task_id=recent, chat_id=message.chat_id)
            return task

        existing_ids = [r["task_id"] for r in await self.db.fetch_all("SELECT task_id FROM tasks")]
        task = make_task(
            task_id=next_task_id(existing_ids),
            chat_id=message.chat_id,
            source_message_id=message.message_id,
            sender_id=message.sender_id,
            intent=classification.intent.value,
            summary=classification.summary or (message.text or ""),
            requested_action=classification.requested_action or (message.text or ""),
            urgency=classification.urgency.value,
            deadline=classification.deadline,
        )
        await db_exec(self.db, task)
        log.info("task_created", task_id=task.task_id, chat_id=task.chat_id, intent=task.intent)
        return task

    async def get(self, task_id: str) -> Task | None:
        row = await self.db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return Task.model_validate(row) if row else None

    async def open_for_message(self, message_id: str) -> Task | None:
        return await self._open_for_source(message_id)

    async def pending_for_chat(self, chat_id: str) -> list[Task]:
        rows = await self.db.fetch_all(
            "SELECT * FROM tasks WHERE chat_id = ? AND status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS') "
            "ORDER BY created_at DESC",
            (chat_id,),
        )
        return [Task.model_validate(r) for r in rows]

    async def list_pending(self, chat_id: str | None = None, aggressive: bool = False, limit: int = 20) -> list[Task]:
        sql = "SELECT * FROM tasks WHERE status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS')"
        params: list = []
        if aggressive:
            sql += " AND urgency IN ('HIGH','URGENT')"
        if chat_id:
            sql += " AND chat_id = ?"
            params.append(chat_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.fetch_all(sql, tuple(params))
        return [Task.model_validate(r) for r in rows]

    async def count_pending(self) -> int:
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS')"
        )
        return int(row["n"]) if row else 0

    async def resolve(self, task_id: str) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        now = ts_now()
        await self.db.execute(
            "UPDATE tasks SET status = ?, resolved_at = ?, updated_at = ? WHERE task_id = ?",
            (TaskStatus.COMPLETED.value, now, now, task_id),
        )
        updated = await self.get(task_id)
        log.info("task_resolved", task_id=task_id, status=updated.status if updated else None)
        return updated

    async def dismiss(self, task_id: str) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        now = ts_now()
        await self.db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (TaskStatus.DISMISSED.value, now, task_id),
        )
        await self._mark_message_done(task.source_message_id)
        return await self.get(task_id)

    async def set_ack_unread_sent(self, task_id: str) -> None:
        await self.db.execute("UPDATE tasks SET ack_unread_sent = 1, updated_at = ? WHERE task_id = ?", (ts_now(), task_id))

    async def set_ack_read_sent(self, task_id: str) -> None:
        await self.db.execute("UPDATE tasks SET ack_read_sent = 1, updated_at = ? WHERE task_id = ?", (ts_now(), task_id))

    async def _open_for_source(self, message_id: str) -> Task | None:
        rows = await self.db.fetch_all(
            "SELECT * FROM tasks WHERE source_message_id = ? "
            "AND status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS') LIMIT 1",
            (message_id,),
        )
        return Task.model_validate(rows[0]) if rows else None

    async def _open_for_chat_recent(self, chat_id: str, minutes: int = 10) -> str | None:
        from datetime import timedelta

        from agent.timeutil import from_ts, ts_now

        now = from_ts(ts_now())
        cutoff = (now - timedelta(minutes=minutes)) if now else None
        rows = await self.db.fetch_all(
            "SELECT task_id, created_at FROM tasks WHERE chat_id = ? "
            "AND status IN ('PENDING','ACKNOWLEDGED','IN_PROGRESS')",
            (chat_id,),
        )
        for r in rows:
            created = from_ts(r["created_at"])
            if cutoff is not None and created is not None and created >= cutoff:
                return r["task_id"]
        return None

    async def _mark_message_done(self, source_message_id: str | None) -> None:
        if source_message_id:
            await self.db.execute(
                "UPDATE monitoring SET state = 'DISMISSED', unread_check_at = NULL, read_unanswered_check_at = NULL, updated_at = ? WHERE message_id = ?",
                (ts_now(), source_message_id),
            )


async def db_exec(db, task: Task) -> None:
    await db.execute(
        "INSERT INTO tasks (task_id, chat_id, source_message_id, sender_id, intent, summary, "
        "requested_action, urgency, deadline, status, ack_unread_sent, ack_read_sent, created_at, updated_at, resolved_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task.task_id,
            task.chat_id,
            task.source_message_id,
            task.sender_id,
            task.intent,
            task.summary,
            task.requested_action,
            task.urgency,
            task.deadline,
            TaskStatus.PENDING.value,
            0,
            0,
            task.created_at,
            task.updated_at,
            task.resolved_at,
        ),
    )