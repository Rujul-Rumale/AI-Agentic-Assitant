"""Persistent entity models (pydantic) mirroring the SQLite schema.

All timestamps are stored as tz-aware ISO-8601 UTC strings (see
`agent.timeutil`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent.timeutil import ts_now


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Chat(Base):
    chat_id: str
    chat_type: str = "UNKNOWN"
    name: str = ""
    participant_name: str = ""
    last_message_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class Message(Base):
    message_id: str
    chat_id: str
    sender_id: str
    timestamp: str | None = None
    received_at: str | None = None
    read_at: str | None = None
    is_read: bool = False
    is_outgoing: bool = False
    agent_sent: bool = False
    chat_type: str = "PRIVATE"
    message_type: str = "TEXT"
    text: str | None = None
    media_reference: str | None = None
    reply_to_message_id: str | None = None
    quoted_message_id: str | None = None
    created_at: str = ""


class Task(Base):
    task_id: str
    chat_id: str
    source_message_id: str | None = None
    sender_id: str = ""
    intent: str = "UNKNOWN"
    summary: str = ""
    requested_action: str = ""
    urgency: str = "NORMAL"
    deadline: str | None = None
    status: str = "PENDING"
    ack_unread_sent: bool = False
    ack_read_sent: bool = False
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str | None = None


class Monitoring(Base):
    message_id: str
    state: str = "RECEIVED"
    unread_check_at: str | None = None
    read_unanswered_check_at: str | None = None
    ack_unread_sent: bool = False
    ack_read_sent: bool = False
    created_at: str = ""
    updated_at: str = ""


class AuditEvent(Base):
    timestamp: str = ""
    event_type: str = ""
    chat_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    details: str | None = None
    result: str | None = None


class AcknowledgementRow(Base):
    message_id: str | None = None
    task_id: str | None = None
    chat_id: str
    kind: str = "UNREAD"  # UNREAD | READ_UNANSWERED
    body: str = ""
    sent_at: str = ""
    dry_run: bool = False
    policy: str | None = None
    result: str = ""


def make_chat(chat_id: str, chat_type: str = "UNKNOWN", name: str = "", participant: str = "") -> Chat:
    now = ts_now()
    return Chat(
        chat_id=chat_id,
        chat_type=chat_type,
        name=name,
        participant_name=participant,
        created_at=now,
        updated_at=now,
    )


def make_message(
    *,
    message_id: str,
    chat_id: str,
    sender_id: str,
    timestamp: str | None = None,
    received_at: str | None = None,
    read_at: str | None = None,
    is_read: bool = False,
    is_outgoing: bool = False,
    agent_sent: bool = False,
    chat_type: str = "PRIVATE",
    message_type: str = "TEXT",
    text: str | None = None,
    media_reference: str | None = None,
    reply_to_message_id: str | None = None,
    quoted_message_id: str | None = None,
) -> Message:
    from agent.timeutil import ts_now

    return Message(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        timestamp=timestamp or ts_now(),
        received_at=received_at or ts_now(),
        read_at=read_at,
        is_read=is_read,
        is_outgoing=is_outgoing,
        agent_sent=agent_sent,
        chat_type=chat_type,
        message_type=message_type,
        text=text,
        media_reference=media_reference,
        reply_to_message_id=reply_to_message_id,
        quoted_message_id=quoted_message_id,
        created_at=ts_now(),
    )


def make_task(
    *,
    task_id: str,
    chat_id: str,
    source_message_id: str | None,
    sender_id: str,
    intent: str,
    summary: str,
    requested_action: str,
    urgency: str = "NORMAL",
    deadline: str | None = None,
) -> Task:
    now = ts_now()
    return Task(
        task_id=task_id,
        chat_id=chat_id,
        source_message_id=source_message_id,
        sender_id=sender_id,
        intent=intent,
        summary=summary,
        requested_action=requested_action,
        urgency=urgency,
        deadline=deadline,
        created_at=now,
        updated_at=now,
    )