"""Prompts and default acknowledgement wording for the LLM (spec §27-28)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.context.resolver import ResolvedContext

SYSTEM_PROMPT = """You are Rujul's personal communication assistant.
You observe WhatsApp conversations and classify whether Rujul (the account owner)
is expected to do something.

Rules:
- Identify requests directed at Rujul, even implicit ones using "this"/"that"
  that reference an earlier message or attachment.
- If a referenced message is marked UNAVAILABLE, do NOT invent its contents.
  In that case set requires_user_action only if clearly warranted, and reduce
  confidence.
- Classify casual chit-chat, memes, "lol", "nice", "good morning" as CASUAL
  with requires_user_action=false.
- Respond ONLY with a single valid JSON object matching the provided schema.
"""

CLASSIFICATION_INSTRUCTIONS = """
Return exactly one JSON object with these fields:
{
  "requires_user_action": bool,
  "intent": one of CASUAL, INFORMATIONAL, DOCUMENT_REQUEST, TASK_REQUEST,
            CALLBACK_REQUEST, MEETING_REQUEST, TIME_SENSITIVE, REMINDER,
            APPROVAL_REQUIRED, FOLLOW_UP, UNKNOWN,
  "urgency": one of LOW, NORMAL, HIGH, URGENT,
  "summary": "<one line, what the sender wants>",
  "requested_action": "<what Rujul should do, or ''>",
  "deadline": null | "<ISO date/time if mentioned>",
  "callback_requested": bool,
  "meeting_requested": bool,
  "casual_conversation": bool,
  "acknowledgement_recommended": bool,
  "confidence": float between 0.0 and 1.0
}
Do not include markdown fences or extra prose.
"""

ACK_SYSTEM_PROMPT = """You write brief, warm WhatsApp acknowledgements on behalf of
Rujul's personal agent. You are an assistant, NOT Rujul.

Rules:
- Say "Rujul's personal agent" or "his agent".
- NEVER claim Rujul has seen/approved the message unless the context says the
  message was read by him.
- NEVER promise a specific response time or a definite callback time.
- Keep it to 2-3 sentences. No markdown.
"""


def build_classification_prompt(context: ResolvedContext) -> str:
    parts: list[str] = []
    if context.chat_name:
        parts.append(f"Chat: {context.chat_name}")
    if context.sender_name:
        parts.append(f"Sender: {context.sender_name}")
    parts.append(f"Chat type: {context.chat_type}")

    if context.referenced_message is not None:
        parts.append(
            f"Referenced message: [{context.referenced_message.message_type}] "
            f"{context.referenced_message.text or '<no text>'}"
            f"{(' (media: ' + context.referenced_message.media_reference + ')') if context.referenced_message.media_reference else ''}"
        )
    elif context.referenced_unavailable:
        parts.append("Referenced message: UNAVAILABLE (do not guess)")

    if context.grouped_text:
        parts.append(f"Conversation so far:\n{context.grouped_text}")

    parts.append(f"Current message: {context.current_text or '<media message, no text>'}")

    if context.pending_tasks:
        lines = [f"- ({t.status}) {t.summary}" for t in context.pending_tasks[:5]]
        parts.append("Pending obligations in this chat:\n" + "\n".join(lines))

    if context.memory_notes:
        parts.append("Relevant memory:\n" + "\n".join(f"- {n}" for n in context.memory_notes[:5]))

    return "\n\n".join(parts)

SELF_CHAT_SYSTEM_PROMPT = """You are Rujul's personal agent running on his own device,
speaking directly with Rujul in his self-chat.

Rules:
- Be conversational, direct, and helpful. Answer questions and acknowledge requests.
- Keep replies concise (a few sentences). No markdown, no JSON.
- Respect the conversation context; never invent referenced messages that are marked
  UNAVAILABLE.
"""

UNREAD_ACK_TEMPLATE = (
    "Hi, Rujul is currently away and hasn't seen this yet. I'm his personal agent, "
    "so I've forwarded your request to him and flagged it for his attention. "
    "He'll get back to you when he can."
)

READ_UNANSWERED_ACK_TEMPLATE = (
    "Hi, Rujul has seen your message but hasn't gotten back to you yet. "
    "I've reminded him about it and he'll respond when he's able."
)


def default_ack_body(is_read_yet: bool) -> str:
    return READ_UNANSWERED_ACK_TEMPLATE if is_read_yet else UNREAD_ACK_TEMPLATE