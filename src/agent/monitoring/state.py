"""Monitoring state machine (spec Â§17-18, Â§25, Â§44-46).

A message enters monitoring when it is relevant (private chat, taggable text).
States:
  RECEIVED -> UNREAD (timer due) -> TRIAGE_PENDING -> TASK_CREATED
           -> ACK_SENT_UNREAD
  TRIAGE_PENDING -> READ_UNANSWERED (on read) -> ACK_SENT_READ -> RESOLVED
Any -> DONE / DISMISSED (no further action required by the agent)

The engine is deterministic: all writes are idempotent, all reads are
conservative, and every decision is gated by the policy engine (via the
AcknowledgementManager) before any external side effect.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from agent.database.models import Message
from agent.database.writes import insert_message, raw_to_message, store_outbound, upsert_chat
from agent.logging_setup import get_logger
from agent.monitoring.unread import read_unanswered_check_time, unread_check_time
from agent.timeutil import ts_now
from agent.whatsapp.events import MessageRead, MessageReceived, OutboundSent

if TYPE_CHECKING:
    from agent.actions.acknowledgement import AcknowledgementManager
    from agent.actions.notifications import NotificationManager
    from agent.config import Settings
    from agent.context.resolver import ContextResolver
    from agent.database.db import Database
    from agent.database.settings_store import SettingsStore
    from agent.llm.interface import LLMProvider
    from agent.memory.manager import MemoryManager
    from agent.reminders import ReminderStore
    from agent.whatsapp.events import EventBus
    from agent.whatsapp.interface import RawMessage, WhatsAppProvider

log = get_logger("agent.monitoring")


def _open_statuses_sql() -> str:
    return "PENDING','ACKNOWLEDGED','IN_PROGRESS"


def _clamped(ts: str, floor: str) -> str:
    from agent.timeutil import from_ts

    t, f = from_ts(ts), from_ts(floor)
    if f is not None and t is not None and t < f:
        return floor
    return ts


class MonitoringEngine:
    def __init__(
        self,
        db: Database,
        provider: WhatsAppProvider,
        llm: LLMProvider,
        settings: Settings,
        bus: EventBus,
        store: SettingsStore,
        notifications: NotificationManager,
        audit: AuditLogger,
        ack: AcknowledgementManager,
        tasks: TaskManager,
        commands: CommandDispatcher,
        memory: MemoryManager | None = None,
        reminders: ReminderStore | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.llm = llm
        self.settings = settings
        self.bus = bus
        self.store = store
        self.notifications = notifications
        self._audit_fn = audit
        self.ack = ack
        self.tasks = tasks
        self.commands = commands
        self.memory = memory
        self.reminders = reminders
        self._recent_self_sends: deque[str] = deque(maxlen=20)

    async def audit(self, event_type: str, **kwargs: object) -> None:
        """Append an audit event (binds the DB handle)."""
        await self._audit_fn(self.db, event_type, **kwargs)

    def _make_resolver(self) -> ContextResolver:
        from agent.context.resolver import ContextResolver

        return ContextResolver(self.db, self.provider, self.tasks, self.memory)

    def _msg_from_raw(self, raw: RawMessage, chat_type: str) -> Message:
        return raw_to_message(raw, chat_type)

    def _msg_from_row(self, row: dict) -> Message:
        return Message.model_validate(row)

    # ---------------------------------------------------------------- events
    async def on_message_received(self, event: MessageReceived) -> None:
        raw = event.raw
        log.debug("message_received", chat_id=raw.chat_id, sender=raw.sender_id, message_id=raw.message_id)

        chat_type = await self._resolve_chat_type(raw.chat_id)
        msg = raw_to_message(raw, chat_type)
        inserted = await insert_message(self.db, msg)

        await upsert_chat(
            self.db,
            chat_id=raw.chat_id,
            chat_type=chat_type or "UNKNOWN",
            name=raw.chat_name or "",
            participant=raw.sender_name or "",
            last_message_at=raw.timestamp,
        )

        if not inserted:
            return  # duplicate delivery

        await self.audit("MESSAGE_RECEIVED", message_id=raw.message_id, chat_id=raw.chat_id)

        if chat_type == "SELF":
            if raw.is_outgoing:
                await self.audit("SELF_SKIP_ECHO", message_id=raw.message_id, chat_id=raw.chat_id)
                await self._set_state(
                    raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None
                )
                return
            if raw.text and raw.text in self._recent_self_sends:
                self._recent_self_sends.remove(raw.text)
                await self.audit("SELF_SKIP_DUP", message_id=raw.message_id, chat_id=raw.chat_id)
                await self._set_state(
                    raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None
                )
                return
            await self._on_self(raw)
            return
        if raw.agent_sent or raw.is_outgoing:
            if raw.is_outgoing and not raw.agent_sent:
                await self._on_user_reply(raw, chat_type)
            return

        if chat_type == "GROUP":
            await self._on_group(raw)
        else:
            await self._on_private(raw)

    async def on_message_read(self, event: MessageRead) -> None:
        message_id = event.message_id
        chat_id = event.chat_id
        read_at = event.read_at or ts_now()

        log.debug("message_read", message_id=message_id)
        row = await self.db.fetch_one("SELECT * FROM messages WHERE message_id = ? COLLATE NOCASE", (message_id,))
        if row is None:
            return
        if not row["read_at"]:
            await self.db.execute(
                "UPDATE messages SET read_at = ?, is_read = 1 WHERE message_id = ? COLLATE NOCASE",
                (read_at, message_id),
            )

        mon = await self.db.fetch_one(
            "SELECT * FROM monitoring WHERE message_id = ? COLLATE NOCASE", (message_id,)
        )
        if mon is None:
            await self.audit("MESSAGE_READ", message_id=message_id, chat_id=chat_id)
            return

        if mon["state"] in ("UNREAD", "TRIAGE_PENDING", "TASK_CREATED", "ACK_SENT_UNREAD"):
            due = read_unanswered_check_time(
                read_at, int(self.settings.monitoring.private_read_unanswered_threshold_minutes)
            )
            await self._set_state(message_id, "READ_UNANSWERED", unread_check_at=None, read_unanswered_check_at=due)

        await self.audit("MESSAGE_READ", message_id=message_id, chat_id=mon["chat_id"], read_at=read_at)

    async def on_outbound_sent(self, event: OutboundSent) -> None:
        await store_outbound(
            self.db,
            message_id=event.message_id,
            chat_id=event.chat_id,
            text=event.text or "",
            sender_id=event.sender_id or "agent",
            chat_type="SELF",
        )

    # ------------------------------------------------------------- internal events
    async def _on_self(self, raw: RawMessage) -> None:
        """Self-chat message -> command dispatch, else NIM-generated dialogue (spec Â§54-56)."""
        response = await self.commands.handle(self, raw)
        if response:
            await self._send(chat_id=raw.chat_id, text=response, force=True)
            await self.audit(
                "SELF_COMMAND_ANSWERED", message_id=raw.message_id, chat_id=raw.chat_id
            )
        else:
            reply = await self._maybe_remind_reply(raw)
            if reply is None:
                reply = await self._maybe_search_reply(raw)
            if reply is None:
                reply = await self._generate_self_reply(raw)
            if reply:
                await self._send(chat_id=raw.chat_id, text=reply, force=True)
                await self.audit(
                    "SELF_CHAT_ANSWERED", message_id=raw.message_id, chat_id=raw.chat_id
                )
        await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)

    async def _maybe_remind_reply(self, raw: RawMessage) -> str | None:
        """Self-chat "remind me to X in N minutes" -> schedule a reminder."""
        if self.reminders is None:
            return None
        from agent.reminders import parse_remind_request

        parsed = parse_remind_request(raw.text or "", timezone=self.settings.timezone)
        if not parsed:
            return None
        task, due_dt, kind = parsed
        from agent.timeutil import from_ts

        reminder = await self.reminders.create(
            task, due_dt, kind=kind, chat_id=raw.chat_id,
        )
        await self.audit(
            "REMINDER_CREATED", message_id=raw.message_id, reminder_id=reminder.reminder_id,
            due=reminder.due_at,
        )
        due = from_ts(reminder.due_at)
        when = due.strftime("%d %b %H:%M") if due else reminder.due_at
        return f"Reminder {reminder.reminder_id} set for {when}: {task}"

    async def _maybe_search_reply(self, raw: RawMessage) -> str | None:
        """Self-chat "search X" / "what did Y say" -> cross-chat search (or None)."""
        from agent.database.search import extract_search_query, format_search_results, search_messages

        query = extract_search_query(raw.text or "")
        if not query:
            return None
        results = await search_messages(
            self.db,
            query,
            exclude_chat_id=self.settings.whatsapp.self_chat_id,
        )
        await self.audit("SELF_SEARCH", message_id=raw.message_id, query=query, hits=len(results))
        return format_search_results(results, query)

    async def _generate_self_reply(self, raw: RawMessage) -> str:
        """Ask NIM to write the reply for a self-chat message (fail-closed on outage)."""
        msg = self._msg_from_raw(raw, "SELF")
        resolver = self._make_resolver()
        ctx = await resolver.resolve(msg, "SELF")
        try:
            return await self.llm.chat_reply(ctx)
        except Exception:  # noqa: BLE001
            log.warning("self_chat_llm_unavailable", message_id=raw.message_id)
            return ""

    async def _on_group(self, raw: RawMessage) -> None:
        if not self.settings.groups.monitor:
            await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return

        if not self._relevant_for_monitoring(raw):
            await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return

        ctx, cls, err = await self._triage(raw, "GROUP")
        if err:
            await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return

        if cls is not None and cls.requires_user_action:
            task = await self.tasks.create(self._msg_from_raw(raw, "GROUP"), cls)
            if task is not None:
                await self.notifications.action_required_group(raw, task, cls)
            await self.audit(
                "GROUP_TRACKED", message_id=raw.message_id, chat_id=raw.chat_id,
                severity=cls.urgency.value,
            )

        await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)

    async def _on_private(self, raw: RawMessage) -> None:
        if not self.settings.monitoring.private_chats:
            await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return
        if not self._relevant_for_monitoring(raw):
            await self._set_state(raw.message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return
        due = unread_check_time(raw.timestamp, int(self.settings.monitoring.private_unread_threshold_minutes))
        await self._set_state(raw.message_id, "UNREAD", unread_check_at=due, read_unanswered_check_at=None)
        await self.audit("MONITORING_SCHEDULED", message_id=raw.message_id, chat_id=raw.chat_id, due=due)

    # ------------------------------------------------------------------- due work
    async def run_due_reminders(self) -> None:
        """Fire reminders whose due time has elapsed into the self-chat."""
        if self.reminders is None:
            return
        fired = await self.reminders.fire_due()
        for r in fired:
            body = f"Reminder: {r.text}"
            await self._send(chat_id=r.chat_id, text=body, force=True)
            await self.audit("REMINDER_FIRED", reminder_id=r.reminder_id, chat_id=r.chat_id)

    async def run_unread_due(self, message_id: str) -> None:
        """Fired when unread_check_at elapses (spec Â§27-32)."""
        mon, msg_row = await self._load(message_id)
        if mon is None or msg_row is None:
            return
        if mon["state"] not in ("UNREAD", "TRIAGE_PENDING"):
            return

        # Re-check read state (race safety)
        if msg_row["read_at"]:
            await self.on_message_read(MessageRead(raw=False, chat_id=msg_row["chat_id"], message_id=message_id))
            return

        if await self.store.is_monitoring_paused():
            await self._reschedule_unread(message_id, minutes=60)
            await self.audit("MONITORING_PAUSED", message_id=message_id)
            return

        msg_model = self._msg_from_row(msg_row)
        ctx, cls, err = await self._triage_msg(msg_model, msg_row["chat_type"])
        if err:
            # LLM temporarily down: hold and retry
            await self._reschedule_unread(message_id, minutes=30)
            return

        await self.audit(
            "LLM_CLASSIFIED", message_id=message_id, chat_id=msg_row["chat_id"],
            intent=cls.intent.value, confidence=cls.confidence,
        )
        await self._set_state(message_id, "TRIAGE_PENDING", unread_check_at=None, read_unanswered_check_at=None)

        if not cls.requires_user_action:
            await self._set_state(message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return

        # Low confidence actionable -> notify self low-confidence, no task (spec Â§35)
        if cls.confidence < self.settings.acknowledgement.minimum_confidence:
            await self.notifications.low_confidence(cls, sender=msg_row["sender_id"])
            await self._set_state(message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            await self.audit("LOW_CONFIDENCE_ACTION", message_id=message_id, chat_id=msg_row["chat_id"])
            return

        task = await self.tasks.create(msg_model, cls)
        if task is None:
            await self._set_state(message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            await self.audit("TASK_ALREADY_EXISTS", message_id=message_id, chat_id=msg_row["chat_id"])
            return

        await self._set_state(message_id, "TASK_CREATED", unread_check_at=None, read_unanswered_check_at=None)
        await self.audit(
            "TASK_CREATED", message_id=message_id, task_id=task.task_id, chat_id=msg_row["chat_id"]
        )

        decision = await self.ack.send_unread(
            msg_model, task, ctx, cls, already_sent=mon["ack_unread_sent"]
        )
        ack_sent = False
        if decision.allowed and decision.decision in ("ALLOW", "DRY_RUN"):
            ack_sent = True
            await self._set_state(message_id, "ACK_SENT_UNREAD", unread_check_at=None, read_unanswered_check_at=None)
            await self._set_state_ack(message_id, ack_unread_sent=1)
            await self.audit(
                "ACK_SENT", message_id=message_id, task_id=task.task_id,
                decision=decision.decision, dry_run=getattr(decision, "dry_run", True),
            )
        else:
            await self._set_state(message_id, "TASK_CREATED", unread_check_at=None, read_unanswered_check_at=None)
            await self.audit(
                "ACK_DEFERRED", message_id=message_id, task_id=task.task_id,
                reason=decision.reason or decision.decision,
            )

        if msg_row["chat_type"] == "PRIVATE":
            await self.notifications.action_required_private(
                task, msg_model, cls, ack_sent=ack_sent
            )

    async def run_read_unanswered_due(self, message_id: str) -> None:
        """Fired when read_threshold elapses and user has not replied (spec Â§33-34)."""
        mon, msg_row = await self._load(message_id)
        if mon is None or msg_row is None:
            return
        if mon["state"] not in ("READ_UNANSWERED", "ACK_SENT_READ"):
            return
        if not msg_row["read_at"]:
            return

        if await self._user_replied_since(msg_row["chat_id"], msg_row["read_at"]):
            await self._resolve_from_reply(msg_row)
            return

        if await self.store.is_ack_paused():
            await self._reschedule_read_unanswered(message_id, minutes=60)
            await self.audit("ACK_PAUSED", message_id=message_id)
            return

        task = await self.tasks.open_for_message(message_id)
        if task is None:
            await self._set_state(message_id, "DONE", unread_check_at=None, read_unanswered_check_at=None)
            return

        msg_model = self._msg_from_row(msg_row)
        ctx, cls, err = await self._triage_msg(msg_model, msg_row["chat_type"])
        if err:
            await self._reschedule_read_unanswered(message_id, minutes=30)
            return

        decision = await self.ack.send_read_unanswered(
            msg_model, task, ctx, cls,
            already_sent=mon["ack_read_sent"],
        )
        if decision.allowed and decision.decision in ("ALLOW", "DRY_RUN"):
            await self._set_state(message_id, "ACK_SENT_READ", unread_check_at=None, read_unanswered_check_at=None)
            await self._set_state_ack(message_id, ack_read_sent=1)
            await self.audit(
                "ACK_SENT_READ", message_id=message_id, task_id=task.task_id,
                decision=decision.decision, dry_run=getattr(decision, "dry_run", True),
            )
        else:
            await self._reschedule_read_unanswered(message_id, minutes=60)
            await self.audit(
                "ACK_DEFERRED_READ", message_id=message_id, task_id=task.task_id,
                reason=decision.reason or decision.decision,
            )

        await self.notifications.read_unanswered_reminder(task, msg_row["read_at"])

    # ----------------------------------------------------------- user reply heuristics
    async def _on_user_reply(self, raw: RawMessage, chat_type: str) -> None:
        """A reply from the owner (outgoing, agent_sent=False) may resolve tasks (spec Â§46)."""
        await self.audit("USER_REPLY", message_id=raw.message_id, chat_id=raw.chat_id)
        pending = await self.tasks.pending_for_chat(raw.chat_id)
        if len(pending) == 1:
            task = pending[0]
            await self.tasks.resolve(task.task_id)
            await self._resolve_monitoring_for_message(task.source_message_id or task.task_id)
            await self.audit(
                "TASK_RESOLVED", task_id=task.task_id, why="USER_REPLY", chat_id=raw.chat_id
            )
        elif len(pending) > 1:
            await self.audit(
                "TASK_ASSOCIATION_AMBIGUOUS", chat_id=raw.chat_id, count=len(pending)
            )

    async def _resolve_from_reply(self, msg_row: dict) -> None:
        task = await self.tasks.open_for_message(msg_row["message_id"])
        if task is not None:
            await self.tasks.resolve(task.task_id)
            await self._resolve_monitoring_for_message(msg_row["message_id"])
            await self.audit("TASK_RESOLVED", message_id=msg_row["message_id"], why="USER_REPLY")

    async def _resolve_monitoring_for_message(self, message_id: str) -> None:
        await self._set_state(message_id, "RESOLVED", unread_check_at=None, read_unanswered_check_at=None)

    # ------------------------------------------------------------- triage helpers
    async def _triage(self, raw: RawMessage, chat_type: str) -> tuple:
        """Resolve context and classify (no retries)."""
        msg = self._msg_from_raw(raw, chat_type)
        return await self._triage_msg(msg, chat_type)

    async def _triage_msg(self, msg: Message, chat_type: str) -> tuple:
        resolver = self._make_resolver()
        ctx = await resolver.resolve(msg, chat_type)
        try:
            cls = await self.llm.classify(ctx)
        except Exception:
            cls = None
        return ctx, cls, (cls is None)

    # ------------------------------------------------------------------ storage
    async def _load(self, message_id: str) -> tuple:
        mon = await self.db.fetch_one(
            "SELECT * FROM monitoring WHERE message_id = ? COLLATE NOCASE", (message_id,)
        )
        msg = await self.db.fetch_one(
            "SELECT * FROM messages WHERE message_id = ? COLLATE NOCASE", (message_id,)
        )
        return mon, msg

    async def _set_state_ack(self, message_id: str, ack_unread_sent: int = 0, ack_read_sent: int = 0) -> None:
        cols, vals = [], []
        if ack_unread_sent:
            cols.append("ack_unread_sent = ?")
            vals.append(1)
        if ack_read_sent:
            cols.append("ack_read_sent = ?")
            vals.append(1)
        if not cols:
            return
        vals.extend([ts_now(), message_id])
        await self.db.execute(
            f"UPDATE monitoring SET {', '.join(cols)}, updated_at = ? WHERE message_id = ? COLLATE NOCASE",
            tuple(vals),
        )

    async def _set_state(
        self,
        message_id: str,
        state: str = "UNREAD",
        unread_check_at: str | None = None,
        read_unanswered_check_at: str | None = None,
    ) -> None:
        now = ts_now()
        await self.db.execute(
            "INSERT INTO monitoring (message_id, state, unread_check_at, read_unanswered_check_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(message_id) DO UPDATE SET state = excluded.state, "
            "unread_check_at = excluded.unread_check_at, "
            "read_unanswered_check_at = excluded.read_unanswered_check_at, "
            "updated_at = excluded.updated_at",
            (message_id, state, unread_check_at, read_unanswered_check_at, now, now),
        )

    async def _reschedule_unread(self, message_id: str, minutes: int) -> None:
        due = unread_check_time(ts_now(), minutes)
        await self._set_state(message_id, "UNREAD", unread_check_at=due, read_unanswered_check_at=None)

    async def _reschedule_read_unanswered(self, message_id: str, minutes: int) -> None:
        due = read_unanswered_check_time(ts_now(), minutes)
        await self._set_state(message_id, "READ_UNANSWERED", unread_check_at=None, read_unanswered_check_at=due)

    async def _user_replied_since(self, chat_id: str, since: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT 1 FROM messages WHERE chat_id = ? AND is_outgoing = 1 AND agent_sent = 0 "
            "AND timestamp >= ? LIMIT 1",
            (chat_id, since),
        )
        return row is not None

    async def _resolve_chat_type(self, chat_id: str) -> str | None:
        try:
            provider_type = await self.provider.get_chat_type(chat_id)
            return provider_type.value if hasattr(provider_type, "value") else str(provider_type)
        except Exception:
            from agent.whatsapp.interface import classify_chat_type

            return classify_chat_type(chat_id)

    async def _send(self, *, chat_id: str, text: str, force: bool = False) -> str | None:
        """Send via provider and record the outbound row (agent_sent)."""
        from agent.whatsapp.interface import ChatType, classify_chat_type

        if not force and self.settings.agent.dry_run:
            await self.audit("DRY_RUN_SUPPRESSED", chat_id=chat_id)
            return None
        is_self = classify_chat_type(chat_id, self.settings.whatsapp.self_chat_id) == ChatType.SELF
        if text and is_self:
            self._recent_self_sends.append(text)
        try:
            message_id = await self.provider.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            await self.audit("SEND_FAILED", chat_id=chat_id, error=str(exc)[:200])
            return None
        if message_id:
            await store_outbound(
                self.db,
                message_id=message_id,
                chat_id=chat_id,
                text=text,
                chat_type=classify_chat_type(
                    chat_id, self.settings.whatsapp.self_chat_id
                ).value,
            )
        return message_id

    def _relevant_for_monitoring(self, raw: RawMessage) -> bool:
        """Pre-filter: what deserves monitoring at all (spec Â§25)."""
        if raw.media_reference and not (raw.text or "").strip():
            return False
        if len((raw.text or "").strip()) < 3:
            return False
        return True


def cls_to_msg(msg_row: dict) -> RawMessage:
    """Adapter: stored dict row -> RawMessage for ack/notify consumers."""
    from agent.whatsapp.interface import RawMessage

    return RawMessage(
        message_id=msg_row["message_id"],
        chat_id=msg_row["chat_id"],
        sender_id=msg_row["sender_id"],
        timestamp=msg_row["timestamp"],
        text=msg_row["text"],
        message_type=msg_row["message_type"],
        media_reference=msg_row["media_reference"],
        is_outgoing=bool(msg_row["is_outgoing"]),
        agent_sent=bool(msg_row["agent_sent"]),
        chat_type=msg_row["chat_type"],
    )