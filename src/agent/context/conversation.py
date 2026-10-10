"""Message grouping and conversation shaping (spec §13)."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.database.models import Message
from agent.timeutil import from_ts

GROUPING_WINDOW_SECONDS = 120.0


@dataclass
class MessageGroup:
    messages: list[Message] = field(default_factory=list)
    sender_id: str = ""
    text: str = ""
    start_at: str | None = None
    end_at: str | None = None

    def add(self, message: Message) -> None:
        if not self.messages:
            self.sender_id = message.sender_id
            self.start_at = message.timestamp
        self.messages.append(message)
        self.end_at = message.timestamp
        if message.text:
            self.text = (self.text + "\n" + message.text) if self.text else message.text

    @property
    def combined_text(self) -> str:
        return self.text


def group_messages(messages: list[Message], window_seconds: float = GROUPING_WINDOW_SECONDS) -> list[MessageGroup]:
    """Group consecutive messages from the same sender within a time window.

    Multiple rapid messages ("hey", "can you send the file", "need it tonight")
    collapse into one logical event while each message remains stored (spec §13).
    """
    if not messages:
        return []
    ordered = sorted(messages, key=lambda m: from_ts(m.timestamp) or from_ts(m.created_at))
    groups: list[MessageGroup] = []
    current = MessageGroup()
    prev_time = None
    for msg in ordered:
        ts = from_ts(msg.timestamp) or from_ts(msg.created_at)
        if current.messages and msg.sender_id == current.sender_id and prev_time and ts:
            gap = (ts - prev_time).total_seconds()
            if gap <= window_seconds:
                current.add(msg)
                prev_time = ts
                continue
        groups.append(current)
        current = MessageGroup()
        current.add(msg)
        prev_time = ts
    if current.messages:
        groups.append(current)
    return [g for g in groups if g.messages]


def conversation_text(messages: list[Message], window_seconds: float = GROUPING_WINDOW_SECONDS) -> str:
    """Render recent messages (grouped) as a compact, bounded context block."""
    lines: list[str] = []
    for group in group_messages(messages, window_seconds):
        who = group.sender_id
        lines.append(f"{who}: {group.combined_text}")
    return "\n".join(lines)