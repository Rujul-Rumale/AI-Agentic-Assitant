"""Command handlers (spec Â§54-56)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.commands.parser import Command
from agent.logging_setup import get_logger
from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.monitoring.state import MonitoringEngine
    from agent.whatsapp.interface import RawMessage

log = get_logger("agent.commands")

_HELP = """Commands:
/status
/pending  (list open tasks)
/urgent   (overdue tasks)
/missed   (ACK_SENT unread + not seen recently)
/pause    (pause all acknowledgements)
/resume   (resume acknowledgements)
/search <query>  (search recent messages across chats)
/chats    (list known chats)
/remind <n>m|h|tomorrow|at HH:MM <text>  (schedule a reminder)
/reminders  (list pending reminders)
/unremind <id>  (cancel a reminder)
/help     (this message)

Task actions:
mark task <id> done
dismiss task <id>
"""


class CommandDispatcher:
    async def handle(self, engine: MonitoringEngine, raw: RawMessage) -> str | None:
        text = (raw.text or "").strip()
        cmd = Command.from_text(text)
        if cmd is None:
            return None
        log.info("command_received", name=cmd.name, args=cmd.args)
        try:
            return await self._dispatch(engine, cmd)
        except Exception as exc:
            log.exception("command_error", name=cmd.name, error=str(exc)[:200])
            return "Command failed. Please try again."

    async def _dispatch(self, engine: MonitoringEngine, cmd: Command) -> str | None:
        name = cmd.name
        if name == "status":
            return await self._status(engine)
        if name == "pending":
            return await self._pending(engine)
        if name == "urgent":
            return await self._overdue(engine, threshold_minutes=60)
        if name == "missed":
            return await self._missed(engine)
        if name == "pause":
            return await self._pause(engine)
        if name == "resume":
            return await self._resume(engine)
        if name == "dismiss":
            return await self._dismiss(engine, cmd.args)
        if name == "resolve":
            return await self._resolve(engine, cmd.args)
        if name == "search":
            return await self._search(engine, cmd.args)
        if name == "chats":
            return await self._chats(engine)
        if name == "remind":
            return await self._remind(engine, cmd.args)
        if name == "reminders":
            return await self._reminders(engine)
        if name == "unremind":
            return await self._unremind(engine, cmd.args)
        if name == "help":
            return _HELP
        return "Unknown command. Type /help."

    async def _status(self, engine: MonitoringEngine) -> str:
        pending = await engine.tasks.count_pending()
        ack_paused = await engine.store.is_ack_paused()
        mon_paused = await engine.store.is_monitoring_paused()
        parts = [f"Pending tasks: {pending}"]
        if ack_paused:
            parts.append("Acknowledgements: paused")
        if mon_paused:
            parts.append("Monitoring: paused")
        if not pending and not ack_paused and not mon_paused:
            parts.append("No pending tasks. All good!")
        return "\n".join(parts)

    async def _pending(self, engine: MonitoringEngine) -> str:
        tasks = await engine.tasks.list_pending()
        if not tasks:
            return "No pending tasks."
        lines = []
        for t in tasks:
            lines.append(f"{t.task_id}: {t.status} â€” {t.chat_id} (since {t.created_at})")
        return "\n".join(lines)

    async def _overdue(self, engine: MonitoringEngine, threshold_minutes: int) -> str:
        from agent.timeutil import from_ts

        tasks = await engine.tasks.list_pending()
        overdue = []
        now = from_ts(ts_now())
        for t in tasks:
            created = from_ts(t.created_at)
            if created is not None and now is not None:
                mins = int((now - created).total_seconds() // 60)
                if mins >= threshold_minutes:
                    overdue.append(f"{t.task_id}: {mins}m old ({t.chat_id})")
        if not overdue:
            return "No overdue tasks."
        return "\n".join(overdue)

    async def _missed(self, engine: MonitoringEngine) -> str:
        from agent.timeutil import from_ts

        rows = await engine.db.fetch_all(
            "SELECT m.message_id, m.chat_id, m.text, m.created_at, mn.ack_unread_sent "
            "FROM monitoring mn JOIN messages m ON m.message_id = mn.message_id COLLATE NOCASE "
            "WHERE mn.state = 'ACK_SENT_UNREAD'",
        )
        if not rows:
            return "No missed messages."
        lines = []
        now = from_ts(ts_now())
        for r in rows:
            created = from_ts(r["created_at"]) if r["created_at"] else None
            age = ""
            if created and now:
                age = f" ({int((now - created).total_seconds() // 60)}m ago)"
            snippet = (r["text"] or "")[:60]
            lines.append(f"{r['message_id']}: {r['chat_id']}{age} â€” {snippet}")
        return "\n".join(lines)

    async def _pause(self, engine: MonitoringEngine) -> str:
        from agent.timeutil import ts_now

        await engine.store.set("acknowledgement.paused", "true")
        await engine.audit("ACK_PAUSED")
        return f"Acknowledgements paused at {ts_now()}."

    async def _resume(self, engine: MonitoringEngine) -> str:
        await engine.store.set("acknowledgement.paused", "false")
        # Re-arm any stalled read_unanswered timers
        now = ts_now()
        rows = await engine.db.fetch_all(
            "SELECT message_id, read_at FROM monitoring "
            "WHERE state = 'ACK_SENT_READ' "
            "OR (read_unanswered_check_at IS NULL AND read_at IS NOT NULL AND state != 'RESOLVED')",
        )
        for r in rows:
            if r["read_at"]:
                from agent.monitoring.unread import read_unanswered_check_time

                due = read_unanswered_check_time(
                    r["read_at"],
                    int(engine.settings.monitoring.private_read_unanswered_threshold_minutes),
                )
                await engine.db.execute(
                    "UPDATE monitoring SET read_unanswered_check_at = ?, state = 'READ_UNANSWERED', "
                    "updated_at = ? WHERE message_id = ? COLLATE NOCASE",
                    (due, now, r["message_id"]),
                )
        await engine.audit("ACK_RESUMED")
        return f"Acknowledgements resumed at {ts_now()}."

    async def _dismiss(self, engine: MonitoringEngine, args: tuple[str, ...]) -> str:
        if not args:
            return "Usage: dismiss task <id>"
        task = await engine.tasks.dismiss(args[0])
        if task is None:
            return f"Task {args[0]} not found."
        await engine.audit("TASK_DISMISSED", task_id=args[0])
        return f"Task {args[0]} dismissed."

    async def _resolve(self, engine: MonitoringEngine, args: tuple[str, ...]) -> str:
        if not args:
            return "Usage: mark task <id> done"
        task = await engine.tasks.resolve(args[0])
        if task is None:
            return f"Task {args[0]} not found."
        await engine.audit("TASK_RESOLVED", task_id=args[0], why="USER_COMMAND")
        return f"Task {args[0]} resolved."

    async def _search(self, engine: MonitoringEngine, args: tuple[str, ...]) -> str:
        query = " ".join(args).strip()
        if not query:
            return "Usage: /search <query>"
        from agent.database.search import format_search_results, search_messages

        results = await search_messages(
            engine.db,
            query,
            exclude_chat_id=engine.settings.whatsapp.self_chat_id,
        )
        await engine.audit("SELF_SEARCH_ANSWERED", query=query, hits=len(results))
        return format_search_results(results, query)

    async def _chats(self, engine: MonitoringEngine) -> str:
        rows = await engine.db.fetch_all(
            "SELECT chat_id, name, participant_name, chat_type, "
            "MAX(last_message_at) last_msg FROM chats GROUP BY chat_id "
            "ORDER BY last_msg DESC LIMIT 25"
        )
        if not rows:
            return "No chats seen yet."
        lines = []
        for r in rows:
            label = r["name"] or r["participant_name"] or r["chat_id"]
            lines.append(f"{r['chat_type']} {label} ({r['chat_id']})")
        return "\n".join(lines)

    async def _remind(self, engine: MonitoringEngine, args: tuple[str, ...]) -> str:
        if engine.reminders is None:
            return "Reminders unavailable."
        from agent.reminders import format_reminders, parse_remind_command
        from agent.timeutil import from_ts

        parsed = parse_remind_command(args, timezone=engine.settings.timezone)
        if not parsed:
            return "Usage: /remind 30m<text> | /remind 2h<text> | /remind tomorrow <text>"
        task, due_dt, kind = parsed
        reminder = await engine.reminders.create(
            task, due_dt, kind=kind,
            chat_id=engine.settings.whatsapp.self_chat_id,
        )
        await engine.audit(
            "REMINDER_CREATED", reminder_id=reminder.reminder_id, due=reminder.due_at
        )
        due = from_ts(reminder.due_at)
        when = due.strftime("%d %b %H:%M") if due else reminder.due_at
        return f"Reminder {reminder.reminder_id} set for {when}: {task}"

    async def _reminders(self, engine: MonitoringEngine) -> str:
        if engine.reminders is None:
            return "Reminders unavailable."
        from agent.reminders import format_reminders

        return format_reminders(await engine.reminders.list_pending())

    async def _unremind(self, engine: MonitoringEngine, args: tuple[str, ...]) -> str:
        if engine.reminders is None:
            return "Reminders unavailable."
        if not args:
            return "Usage: /unremind <id>"
        cancelled = await engine.reminders.cancel(args[0])
        if cancelled is None:
            return f"Reminder {args[0]} not found (or already fired/cancelled)."
        await engine.audit("REMINDER_CANCELLED", reminder_id=args[0])
        return f"Reminder {args[0]} cancelled."