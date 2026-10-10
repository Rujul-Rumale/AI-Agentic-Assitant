"""NVIDIA NIM LLM provider (spec §22-24) via the OpenAI-compatible API.

Credentials are read from the environment only — never from config or code
(spec §22, §53).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import TYPE_CHECKING

from agent.llm.interface import LLMProvider, LLMUnavailable
from agent.llm.prompts import (
    ACK_SYSTEM_PROMPT,
    CLASSIFICATION_INSTRUCTIONS,
    SELF_CHAT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_classification_prompt,
    default_ack_body,
)
from agent.llm.schemas import Classification
from agent.logging_setup import get_logger

if TYPE_CHECKING:
    from agent.context.resolver import ResolvedContext
    from agent.database.models import Task

log = get_logger("agent.llm.nim")

NIM_API_KEY_ENV = "NVIDIA_NIM_API_KEY"


class NimLLMProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._api_key = api_key if api_key is not None else os.environ.get(NIM_API_KEY_ENV, "")
        self._client = None

    @property
    def name(self) -> str:
        return "nvidia_nim"

    @property
    def configured(self) -> bool:
        return bool(self.model and self._api_key)

    def _openai(self):
        if self._client is None:
            if not self.configured:
                raise LLMUnavailable("NVIDIA NIM is not configured (model/API key missing).")
            from openai import AsyncOpenAI  # imported lazily; optional dependency

            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self._api_key, timeout=self.timeout)
        return self._client

    async def _chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        max_tokens: int = 512,
        reasoning_effort: str | None = None,
    ) -> str:
        client = self._openai()
        attempt = 0
        while True:
            try:
                kwargs: dict = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if reasoning_effort:
                    kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
                resp = await client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                content = (msg.content or "").strip()
                if not content:
                    content = (getattr(msg, "reasoning_content", None) or "").strip()
                return content
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt >= self.max_retries:
                    raise LLMUnavailable(f"NIM request failed: {exc}") from exc
                backoff = 0.5 * (2 ** (attempt - 1))
                log.warning("nim_retry", attempt=attempt, error=str(exc))
                await asyncio.sleep(backoff)

    async def classify(self, context: ResolvedContext) -> Classification:
        prompt = build_classification_prompt(context)
        content = await self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": CLASSIFICATION_INSTRUCTIONS + "\n\n" + prompt},
            ],
            max_tokens=512,
        )
        return _parse_classification(content)

    async def summarize(self, context: ResolvedContext) -> str:
        try:
            result = await self.classify(context)
            return result.summary
        except LLMUnavailable:
            return context.current_text or ""

    async def generate_acknowledgement(
        self,
        context: ResolvedContext,
        *,
        task: Task | None,
        is_read_yet: bool,
    ) -> str:
        if not self.configured:
            return default_ack_body(is_read_yet)
        try:
            content = await self._chat(
                [
                    {"role": "system", "content": ACK_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Write an acknowledgement for this chat.\nMessage: {context.current_text or ''}",
                    },
                ],
                json_mode=False,
                max_tokens=256,
                reasoning_effort="low",
            )
            return content.strip() or default_ack_body(is_read_yet)
        except LLMUnavailable:
            return default_ack_body(is_read_yet)

    async def chat_reply(self, context: ResolvedContext) -> str:
        """Write the agent's side of a self-chat conversation (fail-closed)."""
        prompt = build_classification_prompt(context)
        try:
            content = await self._chat(
                [
                    {"role": "system", "content": SELF_CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_mode=False,
                max_tokens=256,
                reasoning_effort="low",
            )
            return (content or "").strip()
        except LLMUnavailable:
            return ""

    async def healthcheck(self) -> bool:
        if not self.configured:
            return False
        try:
            client = self._openai()
            await asyncio.wait_for(client.models.list(), timeout=10)
            return True
        except Exception:  # noqa: BLE001
            log.exception("nim_healthcheck_failed")
            return False


def _parse_classification(content: str) -> Classification:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Malformed NIM output (invalid JSON): {exc}") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("Malformed NIM output (not an object).")
    return Classification.model_validate(data)