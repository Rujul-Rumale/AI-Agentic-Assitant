"""Automatic acknowledgement executor (spec §26-30, §55).

All automatic acknowledgements flow through the policy engine. Nothing is sent
when dry-run is enabled. Rate limits and already-sent flags are re-verified at
send time by the policy engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.actions.auditlog import audit
from agent.policy.permissions import Permission
from agent.timeutil import ts_now

if TYPE_CHECKING:
    from agent.config import Settings
    from agent.context.resolver import ResolvedContext
    from agent.database.db import Database
    from agent.database.models import Message, Task
    from agent.llm.interface import LLMProvider
    from agent.llm.schemas import Classification
    from agent.policy.engine import PolicyDecision, PolicyEngine
    from agent.tasks.manager import TaskManager
    from agent.whatsapp.interface import WhatsAppProvider


class AcknowledgementManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        provider: WhatsAppProvider,
        llm: LLMProvider,
        policy: PolicyEngine,
        tasks: TaskManager,
    ) -> None:
        self.settings = settings
        self.db = db
        self.provider = provider
        self.llm = llm
        self.policy = policy
        self.tasks = tasks

    async def _record_and_send(self, decision: PolicyDecision, *, message: Message, task: Task, kind: str, body: str) -> None:
        dry_run = decision.dry_run
        result = "DRY_RUN" if dry_run else "PENDING"
        sent_id: str | None = None
        if not dry_run:
            sent_id = await self.provider.send_message(task.chat_id, body)
            result = "SENT" if sent_id else "SEND_FAILED"
        await self.db.execute(
            "INSERT INTO acknowledgements (message_id, task_id, chat_id, kind, body, sent_at, dry_run, policy, result) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                message.message_id,
                task.task_id,
                task.chat_id,
                kind,
                body,
                ts_now(),
                1 if dry_run else 0,
                decision.model_dump_json(),
                result,
            ),
        )
        await audit(
            self.db,
            "ACK_SENT" if not dry_run else "ACK_DRY_RUN",
            chat_id=task.chat_id,
            message_id=message.message_id,
            task_id=task.task_id,
            details={"kind": kind, "dry_run": dry_run, "body": body},
            result=result,
        )

    async def send_unread(
        self,
        message: Message,
        task: Task,
        context: ResolvedContext,
        classification: Classification,
        already_sent: bool,
    ) -> PolicyDecision:
        decision = await self.policy.evaluate_ack(
            kind="UNREAD",
            chat_id=task.chat_id,
            task=task,
            classification=classification,
            already_sent=already_sent,
        )
        if not decision.allowed:
            await audit(
                self.db,
                "ACK_DENIED",
                chat_id=task.chat_id,
                message_id=message.message_id,
                task_id=task.task_id,
                details={"permission": Permission.ACK_PRIVATE.value, "kind": "UNREAD", "checks": decision.checks},
                result=f"DENIED:{decision.reason}",
            )
            return decision
        body = await self.llm.generate_acknowledgement(context, task=task, is_read_yet=False)
        await self._record_and_send(decision, message=message, task=task, kind="UNREAD", body=body)
        await self.tasks.set_ack_unread_sent(task.task_id)
        return decision

    async def send_read_unanswered(
        self,
        message: Message,
        task: Task,
        context: ResolvedContext,
        classification: Classification,
        already_sent: bool,
    ) -> PolicyDecision:
        decision = await self.policy.evaluate_ack(
            kind="READ_UNANSWERED",
            chat_id=task.chat_id,
            task=task,
            classification=classification,
            already_sent=already_sent,
        )
        if not decision.allowed:
            await audit(
                self.db,
                "ACK_DENIED",
                chat_id=task.chat_id,
                message_id=message.message_id,
                task_id=task.task_id,
                details={"permission": Permission.ACK_PRIVATE.value, "kind": "READ_UNANSWERED", "checks": decision.checks},
                result=f"DENIED:{decision.reason}",
            )
            return decision
        body = await self.llm.generate_acknowledgement(context, task=task, is_read_yet=True)
        await self._record_and_send(decision, message=message, task=task, kind="READ_UNANSWERED", body=body)
        await self.tasks.set_ack_read_sent(task.task_id)
        return decision