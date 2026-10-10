"""Context resolver (spec §11-12).

Builds a *bounded* context window for triage:
  1. current message
  2. explicitly replied-to / quoted message
  3. recent messages in the same chat (grouped, capped)
  4. pending tasks from the same chat
  5. relevant memory

Referenced messages that cannot be retrieved are marked UNAVAILABLE — their
contents are never invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from agent.context.conversation import conversation_text
from agent.database.models import Message, Task
from agent.logging_setup import get_logger
from agent.whatsapp.interface import ProviderCapabilityError

if TYPE_CHECKING:
    from agent.database.db import Database
    from agent.memory.manager import MemoryManager
    from agent.tasks.manager import TaskManager
    from agent.whatsapp.interface import WhatsAppProvider

log = get_logger("agent.context")

RECENT_LIMIT = 8
PENDING_LIMIT = 5


class ResolvedContext(BaseModel):
    message_id: str
    chat_id: str
    chat_type: str
    chat_name: str = ""
    sender_id: str = ""
    sender_name: str = ""
    current_text: str = ""
    current_message: Message | None = None
    referenced_message: Message | None = None
    referenced_unavailable: bool = False
    referenced_id: str | None = None
    recent_count: int = 0
    grouped_text: str = ""
    pending_tasks: list[Task] = []
    memory_notes: list[str] = []
    missing_reference: bool = False


class ContextResolver:
    def __init__(
        self,
        db: Database,
        provider: WhatsAppProvider,
        tasks: TaskManager,
        memory: MemoryManager | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.tasks = tasks
        self.memory = memory

    async def resolve(self, message: Message, chat_type: str) -> ResolvedContext:
        chat_name = ""
        chat_row = await self.db.fetch_one("SELECT name, participant_name FROM chats WHERE chat_id = ?", (message.chat_id,))
        if chat_row:
            chat_name = chat_row["name"] or chat_row["participant_name"] or message.chat_id

        referenced_id = message.reply_to_message_id or message.quoted_message_id
        referenced, referenced_unavailable = await self._resolve_reference(referenced_id)

        recent = await self.db.fetch_all(
            "SELECT * FROM messages WHERE chat_id = ? AND message_id != ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (message.chat_id, message.message_id, RECENT_LIMIT),
        )
        recent_messages = [Message.model_validate(row) for row in recent]
        recent_messages.reverse()

        if message.text and not referenced and referenced_unavailable is False and recent_messages:
            # Cheap deterministic cue: an unprompted "that/this one" with no
            # available reference is exactly the ambiguous case the spec calls out.
            lower = message.text.lower()
            ambiguous = ("that" in lower or "this" in lower) and len(message.text) < 80
        else:
            ambiguous = False
        if ambiguous and not referenced:
            referenced_unavailable = True
            referenced_id = referenced_id or None

        pending = await self.tasks.pending_for_chat(message.chat_id)
        pending = pending[:PENDING_LIMIT]

        memory_notes: list[str] = []
        if self.memory is not None:
            memory_notes = await self.memory.recall_for(message.sender_id, message.chat_id)

        return ResolvedContext(
            message_id=message.message_id,
            chat_id=message.chat_id,
            chat_type=chat_type,
            chat_name=chat_name,
            sender_id=message.sender_id,
            sender_name=message.sender_id,
            current_text=message.text or "",
            current_message=message,
            referenced_message=referenced,
            referenced_unavailable=referenced_unavailable,
            referenced_id=referenced_id,
            recent_count=len(recent_messages),
            grouped_text=conversation_text(recent_messages) if recent_messages else "",
            pending_tasks=pending,
            memory_notes=memory_notes,
            missing_reference=referenced_unavailable,
        )

    async def _resolve_reference(self, referenced_id: str | None) -> tuple[Message | None, bool]:
        if not referenced_id:
            return None, False
        row = await self.db.fetch_one("SELECT * FROM messages WHERE message_id = ?", (referenced_id,))
        if row:
            return Message.model_validate(row), False
        try:
            raw = await self.provider.get_message(referenced_id)
        except ProviderCapabilityError:
            raw = None
        if raw is not None:
            msg = Message.model_validate(raw.model_dump())
            return msg, False
        log.info("referenced_message_unavailable", referenced_id=referenced_id)
        return None, True