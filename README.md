# Personal WhatsApp AI Agent

A local WhatsApp monitoring and task-management agent that observes inbound messages, triages them via LLM, sends controlled acknowledgements to private chats, and posts actionable summaries to a self-chat. It integrates with OpenClaw (WhatsApp gateway + bridge plugin) and uses NVIDIA NIM as the LLM provider.

## Key features
- State machine for private chats: schedules UNREAD check after threshold, classifies on due, creates tasks for actionable messages, sends first acknowledgement (unread) under policy/rate limits, moves to READ_UNANSWERED on read receipts and can send a second acknowledgement if unanswered.
- Policy-gated actions: acknowledgements and self-notifications go through a deterministic policy engine (confidence/category/chat type/rate limits). dry_run controls external sends.
- Bridge architecture: OpenClaw plugin (bridge/) forwards message_received to Python FastAPI receiver (/events) on localhost:8090 and exposes /wa/send for outbound messages; claims inbound so OpenClaw's own agent never auto-replies.
- Self-chat control: commands (/status, /tasks, /pending, /overdue, /missed, /pause, /resume, /dismiss, /resolve, /search, /chats, /remind, /reminders, /unremind) plus natural language.
- SQLite persistence with WAL, idempotent writes, timers reconstructed on restart.
- Watchdog orchestrator keeps gateway + agent alive with health checks and restart caps.## Architecture
```
WhatsApp <-> OpenClaw Gateway + bridge plugin (localhost:18788) <-> FastAPI receiver (localhost:8090) <-> Python agent (monitoring/state + tasks + policy + LLM)
                                                                                                <-> SQLite
                                                                                                <-> Self-chat
```

## Requirements
- Python 3.11+
- OpenClaw gateway installed with WhatsApp linked
- Bridge plugin loaded from C:\Projects\Agent\bridge
- NVIDIA NIM API key/model (or use mock providers for testing)
- Windows (watchdog is Windows-friendly)

## Setup
1. Clone repo.
2. Copy .env.example to .env and set values (SELF_CHAT_ID, NVIDIA_NIM_API_KEY, NVIDIA_NIM_MODEL). .env is gitignored.
3. pip install -e . (plus optional [llm,dev]).
4. Configure OpenClaw: load bridge from C:\Projects\Agent\bridge; WhatsApp channel dmPolicy=open, allowFrom=["*"], groupPolicy=disabled, pluginHooks.messageReceived=true.
5. Start: python tools/watchdog.py (starts agent and gateway with health checks).

## Configuration
- config/config.yaml: providers, thresholds, rate limits, dry_run.
- .env: overrides/secrets (DATABASE_PATH, LOG_LEVEL, SELF_CHAT_ID, BRIDGE_HOST/PORT, OPENCLAW_BRIDGE_URL, NVIDIA_NIM_*).
- Current: agent.dry_run=false, provider=openclaw, llm=nvidia_nim, private_unread_threshold_minutes=1, private_read_unanswered_threshold_minutes=60.

## Tests
pytest -v (asyncio auto). 60 tests.

## Notes
- Read-unanswered ack relies on bridge forwarding read events (async). If not emitted, that path remains dormant; on-demand polling not implemented.
- Group chats monitored but never auto-acked to; actionable items summarized in self-chat.
- No secrets committed; data/*.db* and .env excluded.