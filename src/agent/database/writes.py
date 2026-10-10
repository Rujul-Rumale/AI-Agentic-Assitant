"""Shared DB write helpers (idempotent inserts)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.database.db import Database
    from agent.database.models import Message
    from agent.whatsapp.interface import RawMessage

_MESSAGE_COLUMNS = (
    "message_id, chat_id, sender_id, timestamp, received_at, read_at, is_read, "
    "is_outgoing, agent_sent, chat_type, message_type, text, media_reference, "
    "reply_to_message_id, quoted_message_id, created_at"
)


async def insert_message(db: Database, msg: Message) -> bool:
    """Insert a message; returns True only if newly inserted (spec §43)."""
    cur = await db.conn.execute(
        f"INSERT OR IGNORE INTO messages ({_MESSAGE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            msg.message_id,
            msg.chat_id,
            msg.sender_id,
            msg.timestamp,
            msg.received_at,
            msg.read_at,
            1 if msg.is_read else 0,
            1 if msg.is_outgoing else 0,
            1 if msg.agent_sent else 0,
            msg.chat_type,
            msg.message_type,
            msg.text,
            msg.media_reference,
            msg.reply_to_message_id,
            msg.quoted_message_id,
            msg.created_at,
        ),
    )
    await db.conn.commit()
    return cur.rowcount > 0


async def store_outbound(
    db: Database,
    *,
    message_id: str,
    chat_id: str,
    text: str,
    sender_id: str = "agent",
    chat_type: str = "SELF",
) -> bool:
    """Record one of our own sent messages (agent_sent=True)."""
    from agent.database.models import make_message

    msg = make_message(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        is_outgoing=True,
        agent_sent=True,
        chat_type=chat_type,
    )
    return await insert_message(db, msg)


async def upsert_chat(
    db: Database,
    *,
    chat_id: str,
    chat_type: str,
    name: str = "",
    participant: str = "",
    last_message_at: str | None = None,
) -> None:
    from agent.timeutil import ts_now

    now = ts_now()
    await db.execute(
        "INSERT OR IGNORE INTO chats (chat_id, chat_type, name, participant_name, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (chat_id, chat_type, name, participant, now, now),
    )
    await db.execute(
        "UPDATE chats SET chat_type = ?, name = CASE WHEN ? != '' THEN ? ELSE name END, "
        "participant_name = CASE WHEN ? != '' THEN ? ELSE participant_name END, "
        "last_message_at = COALESCE(?, last_message_at), updated_at = ? WHERE chat_id = ?",
        (chat_type, name, name, participant, participant, last_message_at, now, chat_id),
    )


def raw_to_message(raw: RawMessage, chat_type: str) -> Message:
    from agent.database.models import make_message

    return make_message(
        message_id=raw.message_id,
        chat_id=raw.chat_id,
        sender_id=raw.sender_id,
        timestamp=raw.timestamp,
        received_at=raw.timestamp,
        is_outgoing=raw.is_outgoing,
        agent_sent=raw.agent_sent,
        chat_type=chat_type,
        message_type=raw.message_type,
        text=raw.text,
        media_reference=raw.media_reference,
        reply_to_message_id=raw.reply_to_message_id,
        quoted_message_id=raw.quoted_message_id,
    )