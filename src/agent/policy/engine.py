"""Deterministic policy engine (spec §55, §7, §8).

The LLM proposes; this engine disposes. All automatic external actions go
through `evaluate_ack` and fail closed on any unmet check. Group chats can
never be auto-replied to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from agent.policy.permissions import DEFAULT_POLICY, Decision, Permission
from agent.timeutil import from_ts, to_ts

if TYPE_CHECKING:
    from agent.config import Settings
    from agent.database.db import Database
    from agent.database.models import Task
    from agent.database.settings_store import SettingsStore
    from agent.llm.schemas import Classification


class PolicyDecision(BaseModel):
    permission: Permission
    allowed: bool
    decision: Decision
    reason: str = ""
    dry_run: bool = False
    checks: dict[str, str] = Field(default_factory=dict)
    confidence: float = 0.0


class PolicyEngine:
    def __init__(self, settings: Settings, db: Database, store: SettingsStore) -> None:
        self.settings = settings
        self.db = db
        self.store = store
        self.policy = dict(DEFAULT_POLICY)

    # ----- base permissions -------------------------------------------------
    def permission(self, perm: Permission) -> Decision:
        return self.policy.get(perm, Decision.DENY)

    def can_send_group(self) -> bool:
        return self.permission(Permission.SEND_GROUP) == Decision.ALLOW

    def can_notify_self(self) -> bool:
        dm = self.settings.agent.dry_run
        return self.permission(Permission.NOTIFY_SELF) == Decision.ALLOW and not dm

    async def _chat_type_of(self, chat_id: str) -> str:
        row = await self.db.fetch_one("SELECT chat_type FROM chats WHERE chat_id = ?", (chat_id,))
        return row["chat_type"] if row else "UNKNOWN"

    # ----- acknowledgement gate (spec §55) -----------------------------------
    async def evaluate_ack(
        self,
        *,
        kind: str,  # "UNREAD" | "READ_UNANSWERED"
        chat_id: str,
        task: Task,
        classification: Classification,
        already_sent: bool,
    ) -> PolicyDecision:
        checks: dict[str, str] = {}
        dry_run = self.settings.agent.dry_run
        decision = Decision.DENY
        reason = ""

        if kind not in {"UNREAD", "READ_UNANSWERED"}:
            reason = "INVALID_KIND"
        elif not self.settings.acknowledgement.enabled:
            reason = "ACK_DISABLED"
        elif await self.store.is_ack_paused():
            reason = "ACK_PAUSED"
        elif await self.store.is_monitoring_paused():
            reason = "MONITORING_PAUSED"
        elif kind == "UNREAD" and not self.settings.acknowledgement.unread.enabled:
            reason = "UNREAD_ACK_DISABLED"
        elif kind == "READ_UNANSWERED" and not self.settings.acknowledgement.read_unanswered.enabled:
            reason = "READ_UNANSWERED_ACK_DISABLED"
        elif already_sent:
            reason = "ALREADY_SENT"
        else:
            chat_type = await self._chat_type_of(chat_id)
            checks["chat_type"] = chat_type
            if chat_type != "PRIVATE":
                reason = f"CHAT_NOT_PRIVATE_{chat_type}"
            elif not self.settings.acknowledgement.category_enabled(classification.intent.value):
                reason = f"INTENT_NOT_ELIGIBLE_{classification.intent.value}"
            elif classification.confidence < self.settings.acknowledgement.minimum_confidence:
                reason = f"LOW_CONFIDENCE_{classification.confidence:.2f}"
            elif await self._rate_limit_exceeded(chat_id):
                reason = "RATE_LIMITED"
            else:
                checks["intent"] = classification.intent.value
                checks["confidence"] = f"{classification.confidence:.2f}"
                checks["threshold"] = f"{self.settings.acknowledgement.minimum_confidence}"
                checks["rate_limit"] = "ok"
                checks["task_status"] = task.status
                decision = Decision.ALLOW if not dry_run else Decision.DRY_RUN

        checks["_decision"] = decision.value
        return PolicyDecision(
            permission=Permission.ACK_PRIVATE,
            allowed=decision in {Decision.ALLOW, Decision.DRY_RUN},
            decision=decision,
            reason=reason,
            dry_run=dry_run,
            checks=checks,
            confidence=classification.confidence,
        )

    async def _rate_limit_exceeded(self, chat_id: str) -> bool:
        cfg = self.settings.rate_limits
        today = to_ts(datetime.now(UTC))[:10]
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM acknowledgements WHERE chat_id = ? AND dry_run = 0 AND sent_at >= ?",
            (chat_id, today),
        )
        if row and int(row["n"]) >= cfg.max_ack_per_chat_per_day:
            return True
        last = await self.db.fetch_one(
            "SELECT sent_at FROM acknowledgements WHERE chat_id = ? AND dry_run = 0 ORDER BY sent_at DESC LIMIT 1",
            (chat_id,),
        )
        if last and last["sent_at"]:
            last_ts = from_ts(last["sent_at"])
            if last_ts is None:
                return False
            minutes = (datetime.now(UTC) - last_ts).total_seconds() / 60.0
            if minutes < cfg.minimum_ack_interval_minutes:
                return True
        return False