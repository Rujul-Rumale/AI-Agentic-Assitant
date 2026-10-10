"""Event types flowing from providers into the state engine (spec §42)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent.whatsapp.interface import RawMessage


@dataclass
class MessageReceived:
    raw: RawMessage


@dataclass
class MessageRead:
    chat_id: str
    message_id: str
    read_at: str | None = None


@dataclass
class OutboundSent:
    chat_id: str
    message_id: str
    text: str
    success: bool


Event = MessageReceived | MessageRead | OutboundSent
EventListener = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-process pub/sub between providers and the engine.

    Keeps the engine decoupled from how events arrive (mock simulation, bridge
    HTTP posts, or future native websockets) — idempotency is enforced by the
    engine keying on `message_id` (spec §43).
    """

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def on(self, event_name: str, callback: EventListener) -> None:
        """Register a callback filtered by event type class name (case/underscore insensitive)."""
        target = event_name.lower().replace("_", "")

        async def _filtered(event: Event) -> None:
            if type(event).__name__.lower().replace("_", "") == target:
                await callback(event)

        self._listeners.append(_filtered)

    async def emit(self, event: Event) -> None:
        await self._queue.put(event)

    def emit_nowait(self, event: Event) -> None:
        """Fire-and-forget emission for unbounded in-process queues."""
        self._queue.put_nowait(event)

    async def pump(self) -> None:
        """Run the delivery loop (started by the application)."""
        while True:
            event = await self._queue.get()
            for listener in self._listeners:
                try:
                    await listener(event)
                except Exception:  # noqa: BLE001 - one bad listener must not stall the bus
                    from agent.logging_setup import get_logger

                    get_logger("agent.bus").exception("event_listener_error", event_type=type(event).__name__)
            self._queue.task_done()

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self.pump())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None