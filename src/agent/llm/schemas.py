"""Structured LLM output schemas (spec §23) with strict validation."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Intent(str, Enum):
    CASUAL = "CASUAL"
    INFORMATIONAL = "INFORMATIONAL"
    DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
    TASK_REQUEST = "TASK_REQUEST"
    CALLBACK_REQUEST = "CALLBACK_REQUEST"
    MEETING_REQUEST = "MEETING_REQUEST"
    TIME_SENSITIVE = "TIME_SENSITIVE"
    REMINDER = "REMINDER"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    FOLLOW_UP = "FOLLOW_UP"
    UNKNOWN = "UNKNOWN"


class Urgency(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Classification(BaseModel):
    """Validated output of the triage LLM call (spec §23)."""

    requires_user_action: bool = False
    intent: Intent = Intent.UNKNOWN
    urgency: Urgency = Urgency.NORMAL
    summary: str = ""
    requested_action: str = ""
    deadline: str | None = None
    callback_requested: bool = False
    meeting_requested: bool = False
    casual_conversation: bool = False
    acknowledgement_recommended: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _coerce_actionable(self) -> Classification:
        if self.casual_conversation and self.requires_user_action:
            # Casual fluff that also seems actionable: keep the action but flag it.
            self.casual_conversation = False
        if self.requires_user_action and not self.requested_action:
            self.requested_action = self.summary
        if not self.requires_user_action and self.intent in {
            Intent.DOCUMENT_REQUEST,
            Intent.TASK_REQUEST,
            Intent.CALLBACK_REQUEST,
            Intent.MEETING_REQUEST,
            Intent.APPROVAL_REQUIRED,
            Intent.FOLLOW_UP,
            Intent.TIME_SENSITIVE,
        }:
            self.requires_user_action = True
        return self


class AckTone(str, Enum):
    UNREAD = "UNREAD"
    READ_UNANSWERED = "READ_UNANSWERED"


def require_enum(value: object, enum_cls: type[Enum], fallback) -> Enum:
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return fallback


AnticipatedField = Literal["requires_user_action", "intent", "urgency", "summary", "confidence"]