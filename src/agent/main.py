"""Application composition (spec Â§58, Â§76-80).

The full local pipeline is assembled here. Every component is behind its
respective provider interface so mocking remains a single config toggle.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from agent.config import load_settings
from agent.daily_greeting import run_daily_greeting
from agent.database.db import Database
from agent.database.migrations import migrate
from agent.database.settings_store import SettingsStore
from agent.logging_setup import configure_logging as setup_structlog
from agent.logging_setup import get_logger
from agent.monitoring.scheduler import Scheduler
from agent.monitoring.state import MonitoringEngine
from agent.timeutil import ts_now
from agent.whatsapp.events import EventBus, MessageReceived
from agent.whatsapp.interface import RawMessage

log = get_logger("agent.main")


async def build_components(config_path: str | Path = "config/config.yaml") -> dict[str, Any]:
    """Load config from file and build the full component graph."""
    return await build_from_settings(load_settings(str(config_path)))


async def build_from_settings(settings) -> dict[str, Any]:
    setup_structlog(settings.log_level)

    db_path = settings.database_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    await db.connect()
    await migrate(db)

    store = SettingsStore(db)
    bus = EventBus()

    # Providers
    if settings.whatsapp.provider == "openclaw":
        from agent.whatsapp.openclaw import OpenClawBridgeProvider
        whatsapp = OpenClawBridgeProvider(
            bridge_url=settings.whatsapp.bridge_url,
            bus=bus,
            self_chat_id=settings.whatsapp.self_chat_id,
        )
    else:
        from agent.whatsapp.mock import MockWhatsAppProvider
        whatsapp = MockWhatsAppProvider(bus=bus, self_chat_id=settings.whatsapp.self_chat_id)

    if settings.llm.provider == "nvidia_nim":
        from agent.llm.nim import NimLLMProvider
        llm = NimLLMProvider(
            model=settings.llm.model,
            base_url=settings.llm.nim_base_url,
        )
    else:
        from agent.llm.mock import MockLLMProvider
        llm = MockLLMProvider()

    # Services
    from agent.actions.acknowledgement import AcknowledgementManager
    from agent.actions.auditlog import audit as audit_fn
    from agent.actions.notifications import NotificationManager
    from agent.commands.handlers import CommandDispatcher
    from agent.memory.manager import MemoryManager
    from agent.policy.engine import PolicyEngine
    from agent.reminders import ReminderStore
    from agent.tasks.manager import TaskManager

    memory = MemoryManager(db, enabled=settings.memory.enabled)
    policy_engine = PolicyEngine(settings=settings, db=db, store=store)
    notifications = NotificationManager(settings=settings, db=db, provider=whatsapp)
    tasks = TaskManager(db=db)
    ack = AcknowledgementManager(
        settings=settings, db=db, provider=whatsapp, llm=llm, policy=policy_engine, tasks=tasks,
    )
    commands = CommandDispatcher()

    engine = MonitoringEngine(
        db=db,
        provider=whatsapp,
        llm=llm,
        settings=settings,
        bus=bus,
        store=store,
        notifications=notifications,
        audit=audit_fn,
        ack=ack,
        tasks=tasks,
        commands=commands,
        memory=memory,
        reminders=ReminderStore(db=db, self_chat_id=settings.whatsapp.self_chat_id),
    )
    scheduler = Scheduler(engine, interval=int(settings.scheduler.poll_interval_seconds))

    # Wire event handlers
    bus.on("message_received", engine.on_message_received)
    bus.on("message_read", engine.on_message_read)
    bus.on("outbound_sent", engine.on_outbound_sent)

    # FastAPI inbound app (openclaw HTTP bridge)
    inbound_app = None
    if settings.whatsapp.provider == "openclaw":
        from agent.whatsapp.openclaw import build_inbound_app
        inbound_app = build_inbound_app(provider=whatsapp)

    return {
        "settings": settings,
        "db": db,
        "bus": bus,
        "whatsapp": whatsapp,
        "llm": llm,
        "engine": engine,
        "scheduler": scheduler,
        "store": store,
        "tasks": tasks,
        "inbound_app": inbound_app,
    }


async def _serve_inbound(app, settings) -> None:
    """Run the FastAPI inbound receiver on the configured bridge host/port."""
    import uvicorn

    config = uvicorn.Config(
        app, host=settings.bridge.host, port=settings.bridge.port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started or task.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        raise RuntimeError("inbound receiver failed to start")


async def run_agent(config_path: str | Path = "config/config.yaml") -> None:
    """Start the agent, run the scheduler, and keep alive until cancelled."""
    parts = await build_components(config_path)
    scheduler: Scheduler = parts["scheduler"]
    db: Database = parts["db"]
    bus: EventBus = parts["bus"]

    bus.start()
    await scheduler.start()
    log.info("agent_started", config=str(config_path))

    greeting_task = asyncio.create_task(run_daily_greeting(parts, hour=5, minute=0))
    log.info("daily_greeting_started")

    inbound_task: asyncio.Task | None = None
    if parts.get("inbound_app") is not None:
        settings = parts["settings"]
        inbound_task = asyncio.create_task(_serve_inbound(parts["inbound_app"], settings))
        log.info(
            "inbound_receiver_started",
            host=settings.bridge.host,
            port=settings.bridge.port,
        )

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        greeting_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await greeting_task
        if inbound_task is not None:
            inbound_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inbound_task
        await scheduler.stop()
        await db.close()
        await bus.stop()
        log.info("agent_stopped")


async def run_demo(config_path: str | Path = "config/config.yaml") -> None:
    """Walk through a representative lifecycle with mock providers (spec Â§65)."""
    parts = await build_components(config_path)
    settings = parts["settings"]
    bus: EventBus = parts["bus"]
    scheduler: Scheduler = parts["scheduler"]
    db: Database = parts["db"]

    bus.start()
    await scheduler.start()

    def _new_raw(text: str, sender: str = "user:alice", chat_id: str = "chat:alice") -> RawMessage:
        return RawMessage(
            message_id=f"mid-{ts_now()}-{sender}",
            chat_id=chat_id,
            sender_id=sender,
            timestamp=ts_now(),
            text=text,
            message_type="TEXT",
        )

    async def _fire(text: str, **kw: Any) -> RawMessage:
        raw = _new_raw(text, **kw)
        await bus.emit(MessageReceived(raw=raw))
        return raw

    def _self_msg(text: str, sender: str = "agent:me") -> RawMessage:
        return RawMessage(
            message_id=f"self-{ts_now()}",
            chat_id=settings.whatsapp.self_chat_id,
            sender_id=sender,
            timestamp=ts_now(),
            text=text,
            message_type="TEXT",
            is_outgoing=True,
            agent_sent=False,
        )

    log.info("demo_started")

    # 1) Single message â€” should be ignored (below batch threshold)
    await _fire("Can we talk later?")
    await asyncio.sleep(0.5)

    # 2) Batch of 3 actionable messages from same sender
    await _fire("Hey, the tickets for the Sat event are live.", sender="user:alice", chat_id="chat:alice")
    await asyncio.sleep(0.5)
    await _fire("Need to book 2x before 3pm.", sender="user:alice", chat_id="chat:alice")
    await asyncio.sleep(0.5)
    await _fire("Also, Mom's birthday is next Tue â€” send something nice.", sender="user:alice", chat_id="chat:alice")
    await asyncio.sleep(0.5)

    # 3) Self-chat commands
    await bus.emit(MessageReceived(raw=_self_msg("/status")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("/tasks")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("/help")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("/pause")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("/resume")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("mark task TASK-0001 done")))
    await asyncio.sleep(0.5)
    await bus.emit(MessageReceived(raw=_self_msg("/status")))
    await asyncio.sleep(1)

    await scheduler.stop()
    await db.close()
    await bus.stop()
    log.info("demo_completed")
