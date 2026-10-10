"""Abstract LLM provider interface (spec §22)."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from agent.llm.schemas import Classification

if TYPE_CHECKING:
    from agent.context.resolver import ResolvedContext
    from agent.database.models import Task


class LLMUnavailable(RuntimeError):
    """Raised when the configured LLM cannot be reached."""


class LLMProvider(abc.ABC):
    @abc.abstractmethod
    async def classify(self, context: ResolvedContext) -> Classification: ...

    @abc.abstractmethod
    async def summarize(self, context: ResolvedContext) -> str: ...

    @abc.abstractmethod
    async def generate_acknowledgement(
        self,
        context: ResolvedContext,
        *,
        task: Task | None,
        is_read_yet: bool,
    ) -> str: ...

    @abc.abstractmethod
    async def chat_reply(self, context: ResolvedContext) -> str: ...

    @abc.abstractmethod
    async def healthcheck(self) -> bool: ...

    @property
    @abc.abstractmethod
    def name(self) -> str: ...