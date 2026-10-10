"""Mock WhatsApp provider (spec §63) for offline development and tests.

Simulates receiving messages, read receipts, user replies, and sends, all
through the same `EventBus` the real bridge uses — so the engine is exercised
identically.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from agent.timeutil import ts_now
from agent.whatsapp import events
from agent.whatsapp.interface import (
    ChatType,
    MockProvider,
    RawMessage,
    classify_chat_type,
)

if TYPE_CHECKING:
    from agent.whatsapp.events import EventBus


class MockWhatsAppProvider(MockProvider):
    def __init__(self, bus: EventBus, self_chat_id: str | None = None) -> None:
        self.bus = bus
        self.self_chat_id = self_chat_id or ""
        self._messages: dict[str, list[RawMessage]] = {}
        self._read: dict[str, bool] = {}
        self.sent: list[RawMessage] = []
        self._ids = itertools.count(1)
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def name(self) -> str:
        return "mock"

    def _store(self, raw: RawMessage) -> None:
        self._messages.setdefault(raw.chat_id, []).append(raw)
        if not raw.is_outgoing and raw.message_id not in self._read:
            self._read[raw.message_id] = False

    async def simulate_receive(
        self,
        *,
        chat_id: str,
        sender_id: str | None = None,
        text: str | None = None,
        message_id: str | None = None,
        timestamp: str | None = None,
        reply_to_message_id: str | None = None,
        quoted_message_id: str | None = None,
        message_type: str = "TEXT",
        media_reference: str | None = None,
    ) -> RawMessage:
        raw = RawMessage(
            message_id=message_id or f"mock-in-{next(self._ids)}",
            chat_id=chat_id,
            sender_id=sender_id or chat_id,
            timestamp=timestamp or ts_now(),
            is_outgoing=False,
            message_type=message_type,
            text=text,
            media_reference=media_reference,
            reply_to_message_id=reply_to_message_id,
            quoted_message_id=quoted_message_id,
        )
        self._store(raw)
        await self.bus.emit(events.MessageReceived(raw=raw))
        return raw

    async def simulate_user_reply(self, *, chat_id: str, text: str) -> RawMessage:
        """Simulate Rujul replying from the linked device (outgoing)."""
        raw = RawMessage(
            message_id=f"mock-out-{next(self._ids)}",
            chat_id=chat_id,
            sender_id=self.self_chat_id or chat_id,
            timestamp=ts_now(),
            is_outgoing=True,
            agent_sent=False,
            text=text,
        )
        self._store(raw)
        await self.bus.emit(events.MessageReceived(raw=raw))
        return raw

    async def mark_read(self, message_id: str, read_at: str | None = None) -> None:
        self._read[message_id] = True
        await self.bus.emit(events.MessageRead(chat_id="", message_id=message_id, read_at=read_at or ts_now()))

    async def send_message(self, chat_id: str, text: str) -> str | None:
        mid = f"mock-sent-{next(self._ids)}"
        raw = RawMessage(
            message_id=mid,
            chat_id=chat_id,
            sender_id=self.self_chat_id or "agent",
            timestamp=ts_now(),
            is_outgoing=True,
            agent_sent=True,
            text=text,
        )
        self._store(raw)
        self.sent.append(raw)
        await self.bus.emit(events.OutboundSent(chat_id=chat_id, message_id=mid, text=text, success=True))
        return mid

    async def get_chat_type(self, chat_id: str) -> ChatType:
        t = classify_chat_type(chat_id, self.self_chat_id)
        return t

    async def get_chat_metadata(self, chat_id: str) -> dict:
        msgs = self._messages.get(chat_id, [])
        names = [m.sender_name for m in msgs if m.sender_name]
        return {"chat_id": chat_id, "name": names[-1] if names else chat_id}

    async def get_messages(self, chat_id: str, limit: int = 20, before: str | None = None) -> list[RawMessage]:
        msgs = [m for m in self._messages.get(chat_id, []) if not before or m.message_id != before]
        return msgs[-limit:]

    async def get_message(self, message_id: str) -> RawMessage | None:
        for msgs in self._messages.values():
            for m in msgs:
                if m.message_id == message_id:
                    return m
        return None

    async def get_read_state(self, message_id: str) -> bool:
        return self._read.get(message_id, False)