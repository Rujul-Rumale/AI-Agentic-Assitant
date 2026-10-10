"""Deterministic mock LLM provider (spec §64) — no network, test-friendly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.llm.interface import LLMProvider
from agent.llm.prompts import default_ack_body
from agent.llm.schemas import Classification, Intent, Urgency

if TYPE_CHECKING:
    from agent.context.resolver import ResolvedContext
    from agent.database.models import Task

_CASUAL_WORDS = {
    "lol", "haha", "nice", "good morning", "good afternoon", "good evening",
    "what's up", "ok", "okay", "k", "cool", "👍", "😂", "🙂", "ty", "thanks",
    "thank you", "welcome", "hi", "hey",
}

_ACTION_WORDS = {
    Intent.CALLBACK_REQUEST: ["call", "call me", "call me back", "ring", "phone me"],
    Intent.MEETING_REQUEST: ["meeting", "available", "free tomorrow", "sync", "catch up", "connect this week", "meet tomorrow"],
    Intent.DOCUMENT_REQUEST: ["send", "document", "report", "file", "pdf", "telemetry", "pcb", "data", "attachment", "sheet", "slides"],
    Intent.APPROVAL_REQUIRED: ["approve", "sign", "review", "approval", "look this over"],
    Intent.TASK_REQUEST: ["can you", "could you", "please", "need", "check", "do you think", "mind", "help"],
    Intent.REMINDER: ["remind", "remember"],
    Intent.TIME_SENSITIVE: ["asap", "urgent", "tonight", "today", "deadline", "by 5", "before", "now"],
}

_ACTIONABLE_INTENTS = {
    Intent.DOCUMENT_REQUEST,
    Intent.TASK_REQUEST,
    Intent.CALLBACK_REQUEST,
    Intent.MEETING_REQUEST,
    Intent.TIME_SENSITIVE,
    Intent.APPROVAL_REQUIRED,
    Intent.FOLLOW_UP,
    Intent.REMINDER,
}


class MockLLMProvider(LLMProvider):
    """Keyword-based classifier with per-text overrides for deterministic tests."""

    def __init__(self, overrides: dict[str, Classification] | None = None) -> None:
        self.overrides = overrides or {}

    @property
    def name(self) -> str:
        return "mock"

    async def healthcheck(self) -> bool:
        return True

    def _override(self, text: str) -> Classification | None:
        return self.overrides.get(text.strip().lower())

    async def classify(self, context: ResolvedContext) -> Classification:
        text = (context.current_text or "").strip().lower()
        override = self._override(context.current_text or "") if not text else self.overrides.get((context.current_text or "").strip().lower())
        if override is not None:
            return override
        return self._classify_keywords(text, context)

    def _classify_keywords(self, text: str, context: ResolvedContext) -> Classification:
        if not text:
            return Classification(
                intent=Intent.INFORMATIONAL,
                summary="Media message with no text.",
                confidence=0.5,
            )

        if any(word in text for word in _CASUAL_WORDS) and not any(
            word in text for word in ("send", "call", "need", "review", "approve", "meeting", "check")
        ):
            return Classification(
                intent=Intent.CASUAL,
                casual_conversation=True,
                summary=text,
                confidence=0.95,
            )

        intent = Intent.UNKNOWN
        for candidate, markers in _ACTION_WORDS.items():
            if any(marker in text for marker in markers):
                intent = candidate
                break

        actionable = intent in _ACTIONABLE_INTENTS
        urgency = Urgency.NORMAL
        if any(marker in text for marker in _ACTION_WORDS[Intent.TIME_SENSITIVE]):
            urgency = Urgency.HIGH

        confidence = 0.95
        if context.referenced_unavailable:
            confidence = 0.55  # never meet the 0.75 threshold when context is missing

        intent = intent if intent != Intent.UNKNOWN else (Intent.TASK_REQUEST if actionable else Intent.CASUAL)
        if not actionable and intent == Intent.UNKNOWN:
            intent = Intent.CASUAL

        return Classification(
            requires_user_action=actionable,
            intent=intent,
            urgency=urgency,
            summary=context.current_text or text,
            requested_action=context.current_text or "",
            acknowledgement_recommended=actionable,
            confidence=confidence,
        )

    async def summarize(self, context: ResolvedContext) -> str:
        return context.current_text or "Message with no text."

    async def generate_acknowledgement(
        self,
        context: ResolvedContext,
        *,
        task: Task | None,
        is_read_yet: bool,
    ) -> str:
        return default_ack_body(is_read_yet)

    async def chat_reply(self, context: ResolvedContext) -> str:
        return f"[mock self-chat reply] {context.current_text or ''}"