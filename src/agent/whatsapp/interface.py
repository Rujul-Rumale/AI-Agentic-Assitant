"""Provider-neutral WhatsApp interface (spec §6) and entity types."""

from __future__ import annotations

import abc
from enum import Enum

from pydantic import BaseModel, ConfigDict


class ChatType(str, Enum):
    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    SELF = "SELF"
    UNKNOWN = "UNKNOWN"


class MessageType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    STICKER = "STICKER"
    LOCATION = "LOCATION"
    OTHER = "OTHER"


class RawMessage(BaseModel):
    """Provider-neutral inbound/outbound message."""

    model_config = ConfigDict(extra="ignore")

    message_id: str
    chat_id: str
    sender_id: str
    timestamp: str | None = None
    is_outgoing: bool = False      # true when the account itself sent it
    agent_sent: bool = False       # true when OUR action executor sent it
    message_type: str = "TEXT"
    text: str | None = None
    media_reference: str | None = None
    reply_to_message_id: str | None = None
    quoted_message_id: str | None = None
    chat_name: str | None = None
    sender_name: str | None = None


class ProviderCapabilityError(RuntimeError):
    """Raised when a provider cannot implement a method (spec §84)."""


class WhatsAppProvider(abc.ABC):
    """Abstraction over the WhatsApp transport (OpenClaw bridge or mock)."""

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def send_message(self, chat_id: str, text: str) -> str | None:
        """Send a plain text message; returns the outbound message id if known."""

    @abc.abstractmethod
    async def get_chat_type(self, chat_id: str) -> ChatType: ...

    @abc.abstractmethod
    async def get_chat_metadata(self, chat_id: str) -> dict: ...

    @abc.abstractmethod
    async def get_messages(self, chat_id: str, limit: int = 20, before: str | None = None) -> list[RawMessage]: ...

    @abc.abstractmethod
    async def get_message(self, message_id: str) -> RawMessage | None: ...

    @abc.abstractmethod
    async def get_read_state(self, message_id: str) -> bool:
        """Return whether the message has been read."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...


class MockProvider(WhatsAppProvider):
    """Marker base for mock/test providers — not used at runtime directly."""

    @property
    def name(self) -> str:
        return "mock"


def normalize_phone_jid(value: str) -> str:
    """Convert a bare E.164 number into a canonical personal-chat JID.

    The bridge webhook delivers DM contacts without a domain (e.g.
    ``+917013910290``); the canonical WhatsApp personal JID is
    ``917013910290@s.whatsapp.net`` (no leading ``+``).
    """
    if not value or "@" in value:
        return value
    digits = value[1:] if value.startswith("+") else value
    if digits.isdigit():
        return f"{digits}@s.whatsapp.net"
    return value


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def classify_chat_type(chat_id: str, self_chat_id: str | None = None) -> ChatType:
    """Deterministic chat-type classification (spec §7).

    - SELF: the configured self-chat ("message yourself") id.
    - GROUP: JIDs ending in ``@g.us`` / containing ``-`` group markers.
    - PRIVATE: individual E.164 numbers or phone-derived JIDs.
    - UNKNOWN otherwise.
    """
    if self_chat_id and chat_id == self_chat_id:
        return ChatType.SELF
    if self_chat_id and _digits_only(chat_id) and _digits_only(chat_id) == _digits_only(self_chat_id):
        return ChatType.SELF
    lower = chat_id.lower()
    if lower.endswith("@g.us") or lower.startswith("group:"):
        return ChatType.GROUP
    if lower.endswith("@c.us") or lower.endswith("@s.whatsapp.net") or lower.startswith("+"):
        return ChatType.PRIVATE
    if lower.startswith("self:") or lower == "self":
        return ChatType.SELF
    return ChatType.UNKNOWN