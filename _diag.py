"""Diagnostic: trace the monitoring flow for a single actionable message."""
import asyncio, tempfile, json
from pathlib import Path

async def main():
    d = Path(tempfile.mkdtemp())
    from tests.conftest import make_settings
    from agent.main import build_from_settings
    parts = await build_from_settings(make_settings(d))
    bus = parts['bus']; db = parts['db']
    bus.start(); await parts['scheduler'].start()

    from agent.whatsapp.interface import RawMessage
    from agent.whatsapp.events import MessageReceived
    from agent.timeutil import ts_now
    raw = RawMessage(
        message_id='m-1', chat_id='chat:alice', sender_id='user:alice',
        timestamp=ts_now(),
        text='Can you send me the latest telemetry report asap?',
        message_type='TEXT',
    )
    await bus.emit(MessageReceived(raw=raw))
    await asyncio.sleep(2.0)

    rows = await db.fetch_all("SELECT event_type, details FROM events WHERE message_id = ? ORDER BY timestamp", ('m-1',))
    for r in rows:
        d_ = r['details']
        if d_:
            try:
                d_ = json.loads(d_)
            except Exception:
                pass
        print(f"{r['event_type']:30s}  details={d_}")

    chat_row = await db.fetch_one("SELECT chat_id, chat_type FROM chats")
    print('\n--- chats', dict(chat_row) if chat_row else None)

    await parts['scheduler'].stop(); await db.close(); await bus.stop()

asyncio.run(main())
