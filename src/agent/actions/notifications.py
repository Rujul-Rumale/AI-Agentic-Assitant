"""Self-chat notifications and human-readable status templates (spec §36-37, §69-71)."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from agent.actions.auditlog import audit
from agent.timeutil import from_ts

if TYPE_CHECKING:
    from agent.config import Settings
    from agent.database.db import Database
    from agent.database.models import Message, Task
    from agent.llm.schemas import Classification
    from agent.whatsapp.interface import WhatsAppProvider


# ----- pure formatting helpers -----------------------------------------------
def fmt_action_required_private(task: Task, chat_name: str, classification: Classification, ack_sent: bool) -> str:
    if classification.intent.value == "DOCUMENT_REQUEST" or (classification.requested_action or classification.summary):
        wants = classification.requested_action or classification.summary
    else:
        wants = task.requested_action or task.summary
    lines = [
        "ACTION REQUIRED",
        "",
        f"{chat_name} wants:",
        wants,
        "",
        f"Urgency: {task.urgency}",
        f"Intent: {task.intent}",
        f"Acknowledgement: {'Sent' if ack_sent else 'Pending'}",
        "",
        f"Task: {task.task_id}",
    ]
    return "\n".join(lines)


def fmt_action_required_group(group_name: str, sender: str, quote: str, task: Task, classification: Classification) -> str:
    return "\n".join(
        [
            "ACTION REQUIRED — GROUP",
            "",
            group_name,
            "",
            f"{sender} asked:",
            f"\"{quote}\"",
            "",
            f"Classification:\n{classification.intent.value}",
            "",
            f"Task {task.task_id} created.",
            "",
            "No message was sent to the group.",
        ]
    )


def fmt_low_confidence(classification: Classification, sender: str) -> str:
    return "\n".join(
        [
            "Potential action, low confidence.",
            "",
            f"Sender: {sender}",
            "",
            classification.summary or "(no summary)",
            "",
            "I have not acknowledged this automatically.",
        ]
    )


def fmt_read_unanswered_reminder(task: Task, chat_name: str, read_minutes: int, sender_name: str | None = None) -> str:
    who = sender_name or chat_name
    return "\n".join(
        [
            "REMINDER",
            "",
            f"You read a message from {who} {read_minutes}m ago but haven't replied.",
            "",
            f"Request:\n{task.requested_action or task.summary}",
            "",
            f"A gentle reminder has been sent to {who} that you'll get back to them.",
        ]
    )


def fmt_nim_unavailable() -> str:
    return "NIM was unavailable, so no message was classified. No automatic action taken."


# ----- manager ----------------------------------------------------------------
class NotificationManager:
    def __init__(self, settings: Settings, db: Database, provider: WhatsAppProvider) -> None:
        self.settings = settings
        self.db = db
        self.provider = provider

    @property
    def self_chat_id(self) -> str:
        return self.settings.whatsapp.self_chat_id

    async def _chat_name(self, chat_id: str) -> str:
        row = await self.db.fetch_one("SELECT name, participant_name FROM chats WHERE chat_id = ?", (chat_id,))
        if not row:
            return chat_id
        return row["name"] or row["participant_name"] or chat_id

    async def notify_self(self, body: str, *, force: bool = False) -> None:
        """Send a notification to the self-chat.

        Suppressed (but logged) in dry-run unless ``force`` (user-initiated
        command replies). Fails closed if no self-chat is configured.
        """
        if not self.self_chat_id:
            await audit(self.db, "SELF_NOTIFY_SKIPPED", details={"reason": "NO_SELF_CHAT_CONFIGURED"})
            return
        if not self.settings.notifications.self_chat:
            await audit(self.db, "SELF_NOTIFY_SKIPPED", details={"reason": "NOTIFICATIONS_DISABLED"})
            return
        if self.settings.agent.dry_run and not force:
            await audit(
                self.db,
                "SELF_NOTIFY_DRY_RUN",
                chat_id=self.self_chat_id,
                details={"body": body},
                result="DRY_RUN",
            )
            return
        mid = await self.provider.send_message(self.self_chat_id, body)
        await audit(
            self.db,
            "SELF_NOTIFY_SENT",
            chat_id=self.self_chat_id,
            message_id=mid,
            details={"body": body},
            result="SENT" if mid else "SEND_FAILED",
        )

    async def action_required_private(
        self, task: Task, message: Message, classification: Classification, ack_sent: bool
    ) -> None:
        chat_name = await self._chat_name(task.chat_id)
        await self.notify_self(fmt_action_required_private(task, chat_name, classification, ack_sent))

    async def action_required_group(
        self, message: Message, task: Task, classification: Classification
    ) -> None:
        chat_name = await self._chat_name(message.chat_id)
        quote = message.text or "(media message)"
        await self.notify_self(fmt_action_required_group(chat_name, message.sender_id, quote, task, classification))

    async def low_confidence(self, classification: Classification, sender: str) -> None:
        await self.notify_self(fmt_low_confidence(classification, sender))

    async def nim_unavailable(self) -> None:
        await self.notify_self(fmt_nim_unavailable())

    async def read_unanswered_reminder(self, task: Task, read_at: str | None) -> None:
        chat_name = await self._chat_name(task.chat_id)
        parsed = from_ts(read_at)
        minutes = 0
        if parsed:
            from datetime import datetime

            minutes = int((datetime.now(UTC) - parsed).total_seconds() // 60)
        await self.notify_self(fmt_read_unanswered_reminder(task, chat_name, minutes))