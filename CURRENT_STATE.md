# CURRENT STATE (as of 2026-10-10)

This document summarizes what is currently implemented and running.

## Stack & Runtime
- Python 3.11.9, project package under src/agent (editable install).
- Orchestrator: tools/watchdog.py (Windows-friendly, CREATE_NEW_PROCESS_GROUP).
  - Polls 60s, per-service cooldown 90s, daily cap 20, consecutive cap 10.
  - Starts: Python agent (python -m agent, cwd C:\Projects\Agent, logs to %TEMP%\opencode\agent.log/err, health http://127.0.0.1:8090/healthz).
  - Starts: OpenClaw gateway via C:\Users\rujul\AppData\Roaming\npm\openclaw.cmd gateway run (cwd project, logs to %TEMP%\opencode\gw.log/err, health http://127.0.0.1:18788/wa/ping, POST {} for health check).
- Inbound receiver: FastAPI app on 127.0.0.1:8090 (/healthz and /events). Started by agent on openclaw provider.
- OpenClaw bridge plugin (bridge/): TypeScript plugin whatsapp-agent-bridge.
  - Hooks: message_received POSTs {type:"message", data: ...} to http://127.0.0.1:8090/events.
  - before_dispatch claims WhatsApp inbound ({handled:true}) so OpenClaw's own agent never auto-replies.
  - HTTP server on 127.0.0.1:18788: /wa/__ping, /wa/ping, /wa/debug, POST /wa/send, /wa/chat_metadata, /wa/messages, /wa/message.
  - Lazy runtime resolution to call WhatsApp send; plugin loads from C:\Projects\Agent\bridge.
- Providers: WhatsApp=openclaw (active), LLM=nvidia_nim (DeepSeek v4 flash 0731).
- Current live status: watchdog, agent, gateway UP (ports 8090/18788/18789 listening; health OK).

## Core agent (src/agent)
Top-level: config.py (YAML+env, dotenv load, typed Settings), main.py (composition, FastAPI inbound, run_agent/run_demo), __main__.py, logging_setup.py, daily_greeting.py, reminders.py, timeutil.py.### WhatsApp
- interface.py: WhatsAppProvider abstract, RawMessage (Pydantic), chat type classification, normalization.
- openclaw.py: OpenClawBridgeProvider (HTTP client to bridge /wa/*, raises ProviderCapabilityError if unreachable). Ingests bridge payloads into bus; get_read_state() raises (async events). build_inbound_app() creates FastAPI receiver.
- mock.py, events.py.

### Database
- Migrations 001_initial.sql, 002_reminders.sql. Modules: db.py, models.py, migrations.py, settings_store.py, writes.py, search.py (aiosqlite + WAL).

### Monitoring (state machine)
- Private: UNREAD scheduled at +private_unread_threshold_minutes (1). On due: triage/classify; actionable + confidence>=threshold -> task + ack.send_unread() + ACTION REQUIRED self-chat (private). Low confidence actionable -> self notify + DONE (no task). Read state races handled.
- Read: moves to READ_UNANSWERED at read+read_unanswered_threshold_minutes (60). On due: if user replied since read -> resolve; else ack.send_read_unanswered(), read_unanswered_reminder, reschedule on defer.
- Groups: monitored; actionable -> task + group self-notification; no auto-send to group.
- Self-chat: commands handled or reminder/search/self-LLM reply; echo/dedup suppressed.
- Scheduler: polls due (monitoring/tasks/reminders) every 1.0s.

### Policy & Acks
- policy/engine.py: evaluate_ack() checks enabled/not paused, kind/category/confidence/private/rate limits, not already sent. Returns PolicyDecision (ALLOW/DRY_RUN/DENY), dry_run from settings.agent.dry_run. can_notify_self() requires NOTIFY_SELF ALLOW and not dry_run.
- actions/acknowledgement.py: generates ack via LLM; sends only if not dry_run; records dry_run/result to acknowledgements.
- actions/notifications.py: self-chat notifications (ACTION REQUIRED private/group, low confidence, NIM unavailable, read-unanswered reminder). notify_self() suppresses in dry_run unless force=True.
- Rate limits: 2 acks/chat/day, 60m min interval.

### LLM, Tasks, Context, Memory, Commands
- LLM: interface/mock/nim (OpenAI-compatible), schemas, prompts. healthcheck/classify/generate_acknowledgement/chat_reply with Pydantic validation.
- Tasks stateful, idempotent; resolved on user reply. Commands cover status/pending/overdue/missed/pause/resume/dismiss/resolve/search/chats/remind/reminders/unremind + natural language.
- Context resolver builds history + quoted + pending tasks. Memory optional.## Config & Bridge
- config/config.yaml: agent.dry_run=false, self_chat_id empty in YAML, provider=openclaw, llm=nvidia_nim, monitoring thresholds 1m/60m, poll 1.0s.
- .env (local, excluded): SELF_CHAT_ID=917013910290@s.whatsapp.net, NVIDIA_NIM_API_KEY=..., NVIDIA_NIM_MODEL=deepseek-ai/deepseek-v4-flash-0731, DATABASE_PATH=./data/agent.db, BRIDGE_HOST/PORT, OPENCLAW_BRIDGE_URL.
- bridge/: index.ts (hooks + HTTP server 18788, before_dispatch claim, /wa/* endpoints), package.json, openclaw.plugin.json. Loaded from C:\Projects\Agent\bridge; OpenClaw WhatsApp has dmPolicy=open, allowFrom=["*"], groupPolicy=disabled, messageReceived hook true.

## Tests
60 tests in tests/ (commands, daily greeting, mock providers, monitoring, openclaw ingest, policy, reminders, search, tasks), pytest + asyncio auto.

## Notes
- Read-unanswered ack path exists and gated; relies on bridge forwarding MessageRead events (async). If bridge does not emit read events, it remains dormant. No on-demand polling implemented by design.
- Secrets: only in .env (gitignored). No hardcoded secrets in tracked files.