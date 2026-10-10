# IMPLEMENTATION PLAN — Personal WhatsApp AI Agent

> Generated from environment audit and specification analysis.

---

## 1. Environment Findings

| Component | Value |
|-----------|-------|
| OS | Windows 11 (RUJULS_GTBOOK) |
| Python | 3.11.9 |
| Git | 2.53.0.windows.2 |
| Docker | Not installed |
| pip | 24.0 |
| Project dir | `C:\Projects\Agent` (empty) |

**Notes:**
- No WhatsApp/OpenClaw packages currently installed
- No existing code in the repository
- Docker not required for laptop development; will be needed for Pi deployment

---

## 2. Architecture Summary

```
WhatsApp ←→ OpenClaw Gateway ←→ Our Agent ←→ NVIDIA NIM
                       |                |
                       v                v
                  openclaw-sdk      openai client
                  (channel mgmt)    (chat completions)
```

**Key decisions:**
- Use `openclaw-sdk` (2.1.0) for WhatsApp channel management and gateway communication
- Use OpenAI Python client for NIM (OpenAI-compatible API)
- SQLite via `aiosqlite` for persistent storage
- `asyncio` for concurrent timer/event management

---

## 3. Dependency Plan

### Core dependencies

```
fastapi>=0.115               # event webhook receiver
uvicorn>=0.30                # ASGI server for the webhook receiver
openai>=1.0                  # NIM client (OpenAI-compatible)
aiosqlite>=0.19              # async SQLite
pydantic>=2.0                # data validation
pyyaml>=6.0                  # config files
python-dotenv>=1.0           # env vars
structlog>=24.0              # structured logging
```

### Bridge plugin (TypeScript, runs inside OpenClaw gateway)

```
@openclaw/plugin-sdk        # typed plugin API: api.on("message_received"), HTTP routes
```

Not a Python dependency. Repo layout: `bridge/` subdirectory with its own
`package.json`, installed into the OpenClaw gateway during Phase 1.

### Dev dependencies

```
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=5.0
ruff>=0.5
mypy>=1.10
```

### Optional (future)

```
openclaw-sdk>=2.1.0          # channel status/login/logout, if used for ops
```

---

## 4. WhatsApp Integration Approach

> **Updated after research (2026-09-16).** OpenClaw exposes a documented
> `message_received` plugin hook (opt-in via
> `channels.whatsapp.pluginHooks.messageReceived: true`) that observes every
> inbound message with structured metadata: `content`, `senderId`, `threadId`,
> `messageId`, `replyToId`, `replyToBody`, `replyToSender`, `replyToIsQuote`,
> and `media[]`. This is the correct event surface for a passive monitoring
> agent — no agent pipeline, no LLM round-trip, no session creation.
>
> This is a **proven, previously-implemented pattern**: OpenClaw's
> WhatsApp-observer plugins (e.g. `wa-pro`) capture every message at the
> Baileys wire level via listener taps + the `message_received`/`message_sent`
> hooks, log to SQLite, and never send. We follow the same pattern and route
> events to our Python brain instead of SQLite.

### Verified OpenClaw event surface

| Capability | Availability | How |
|---|---|---|
| Inbound message events | ✅ Documented | `api.on("message_received")` typed plugin hook (opt-in flag) |
| Via internal hook | ✅ Documented | `message:received` / `message:sent` / `message:transcribed` |
| Quote/reply context | ✅ Documented | Hook exposes `replyToId`, `replyToBody`, `replyToSender`, `replyToIsQuote` |
| Media references | ✅ Documented | `media?: PluginHookMediaFact[]`, saved via normal inbound media store |
| Outbound send | ✅ Documented | Plugin `api.runtime.channel.whatsapp.sendMessageWhatsApp(...)` behind an HTTP route |
| Per-message tokenized read receipt | ⚠️ **Unverified** | Not listed in hook docs; may require a Baileys wire-level tap in the plugin. Track as a capability check during Phase 1. |

### Recommended architecture: OpenClaw Gateway Bridge

```
┌─ OpenClaw Gateway (battle-tested, Baileys) ──────────────┐
│  @openclaw/whatsapp channel                              │
│    pluginHooks.messageReceived: true                     │
│  Custom plugin: whatsapp-bridge (TypeScript)             │
│    api.on("message_received") ── HTTP POST ──┐           │
│    api.on("message_transcribed")             │           │
│    HTTP route POST /wa/send ── sendMessageWhatsApp()     │
└────────────┬─────────────────────────────────┼───────────┘
             │                                 │
             │ loopback webhooks               │ loopback HTTP
             ▼                                 ▼
┌─ Python Agent (our application) ─────────────────────────┐
│  Event receiver (fastapi/aiohttp)                        │
│  SQLite (messages, tasks, events, acks)                  │
│  State machine + persistent timers                       │
│  Context resolver → NIM → Pydantic → Policy engine       │
└──────────────────────────────────────────────────────────┘
```

**Inbound (push):** the bridge plugin forwards every wire event to our Python
agent over loopback HTTP. No OpenClaw agent/session is created for monitored
messages — pure observation.

**Outbound (pull):** our Python agent calls `POST /wa/send` on the bridge for
any permitted action (acknowledgements, self-chat notifications). The bridge
is the only process with WhatsApp send capability.

**Why not "execute an OpenClaw agent to send"?**
Running an LLM-backed agent turn just to deliver a fixed policy-approved
acknowledgement is wasteful, non-deterministic, and routes through a
conversational pipeline that could rewrite or refuse the message. The bridge's
direct `sendMessageWhatsApp` call is deterministic and matches the spec's
"structured decision → policy engine → action executor" rule.

**Why not `openclaw-sdk` for sends?** The SDK covers channel *status/login/
logout* and agent execution, but not arbitrary ad-hoc message sends. The bridge
HTTP route is the documented low-level path.

### Read-state capability

The spec's read-unanswered logic needs to know when Rujul's client reads an
inbound message. OpenClaw's typed hook list does **not** clearly expose a
read-receipt event. Two paths:

1. **Bridge taps Baileys `messages.update` / receipt events** in the plugin
   socket (same approach observer plugins use) and forwards read-state to the
   agent. Requires verification of what the plugin SDK exposes — Phase 1 task.
2. **Fallback for MVP:** treat "user sent a reply" as read+answered, and
   approximate read detection from read-receipt polling if path 1 is blocked.

If path 1 is impossible with stock OpenClaw, this is reported as a
**CAPABILITY BLOCKER** (per spec §84) with adapter options — not silently
faked in production code.

### Getting the agent connected on the laptop

Phase 1 requires the OpenClaw gateway + WhatsApp link. Until that is installed
and QR-paired, all development uses `MockWhatsAppProvider` (spec §63) speaking
the same event contract as the bridge. The `WhatsAppProvider` interface keeps
the mock and the real bridge interchangeable in tests.
3. Run a lightweight OpenClaw agent that:
   - Receives inbound messages
   - Forwards them to our agent via HTTP webhook
   - Receives outbound commands from our agent

---

## 5. Development Phases

### Phase 0 — Foundation (Week 1)

**Goal:** `python -m agent` starts successfully.

- [ ] Initialize git repository
- [ ] Create `pyproject.toml` with dependencies
- [ ] Create `.env.example` and `.gitignore`
- [ ] Implement `config/config.yaml` loading
- [ ] Implement structured logging
- [ ] Set up SQLite database with migrations
- [ ] Create application entrypoint (`main.py`)
- [ ] Set up test framework

**Deliverable:** Empty agent starts, loads config, connects to DB, exits cleanly.

### Phase 1 — WhatsApp Adapter (Week 1-2)

**Goal:** Receive and store WhatsApp messages.

- [ ] Install OpenClaw + WhatsApp plugin
- [ ] Implement `WhatsAppProvider` interface
- [ ] Implement OpenClaw adapter
- [ ] Implement message receiving via webhook
- [ ] Implement message sending via SDK
- [ ] Implement chat metadata retrieval
- [ ] Implement chat type classification
- [ ] Implement self-chat detection

**Deliverable:** WhatsApp → Agent → Database works.

### Phase 2 — Database (Week 2)

**Goal:** Persistent state survives restart.

- [ ] Implement `chats` table
- [ ] Implement `messages` table
- [ ] Implement `tasks` table
- [ ] Implement `events` table
- [ ] Implement `acknowledgements` table
- [ ] Implement idempotency checks
- [ ] Test restart recovery

**Deliverable:** All state persists in SQLite.

### Phase 3 — Context Resolution (Week 2-3)

**Goal:** Agent understands message context.

- [ ] Implement reply-to relationship resolution
- [ ] Implement quoted message resolution
- [ ] Implement recent conversation context
- [ ] Implement message grouping
- [ ] Implement pending task context
- [ ] Bounded context window

**Deliverable:** "Can you send that?" understood in context of "Here is the PCB."

### Phase 4 — Read/Unread State (Week 3)

**Goal:** Reliable unread/read detection.

- [ ] Implement unread tracking (received_at, read_at)
- [ ] Implement read detection
- [ ] Implement reply detection
- [ ] Implement persistent timers (SQLite-backed)
- [ ] Implement timer reconstruction on restart

**Deliverable:** Agent knows "unread for 12 min" and "read for 37 min no reply."

### Phase 5 — NIM Integration (Week 3-4)

**Goal:** LLM classifies messages correctly.

- [ ] Implement `NIMProvider` (OpenAI-compatible)
- [ ] Implement structured JSON output
- [ ] Implement Pydantic validation
- [ ] Implement retry logic with backoff
- [ ] Implement timeout handling
- [ ] Implement confidence thresholding
- [ ] Create classification prompts

**Deliverable:** "Can you send the report?" → DOCUMENT_REQUEST, confidence 0.95.

### Phase 6 — Task Engine (Week 4)

**Goal:** Tasks created and tracked.

- [ ] Implement task creation
- [ ] Implement task state management
- [ ] Implement self-chat notifications
- [ ] Implement duplicate prevention
- [ ] Implement task resolution
- [ ] Implement task dismissal

**Deliverable:** Actionable messages create tracked tasks.

### Phase 7 — Acknowledgements (Week 4-5)

**Goal:** Autonomous acknowledgements work.

- [ ] Start with DRY RUN mode
- [ ] Implement first acknowledgement (unread)
- [ ] Implement second acknowledgement (read-unanswered)
- [ ] Implement rate limiting
- [ ] Implement policy engine checks
- [ ] Verify generated messages
- [ ] Enable production mode

**Deliverable:** Appropriate acknowledgements sent automatically.

### Phase 8 — Self-Chat Commands (Week 5)

**Goal:** Control interface works.

- [ ] Implement `/status` command
- [ ] Implement `/tasks` command
- [ ] Implement `/urgent` command
- [ ] Implement `/missed` command
- [ ] Implement `/pause` and `/resume`
- [ ] Implement natural language commands
- [ ] Implement task completion via self-chat

**Deliverable:** User can query and control agent via self-chat.

### Phase 9 — Testing & Polish (Week 5-6)

**Goal:** Reliable, testable system.

- [ ] Comprehensive unit tests for state machine
- [ ] Comprehensive unit tests for policy engine
- [ ] Mock WhatsApp provider for testing
- [ ] Mock LLM provider for testing
- [ ] Integration tests
- [ ] Error handling hardening
- [ ] Logging audit trail

**Deliverable:** High confidence in system behavior.

### Phase 10 — Raspberry Pi Deployment (Week 6+)

**Goal:** Always-on service.

- [ ] Create Dockerfile (or systemd service)
- [ ] Test on Pi hardware
- [ ] Implement health check
- [ ] Implement graceful shutdown
- [ ] Implement reconnection handling
- [ ] Configure systemd service
- [ ] Test reboot recovery

**Deliverable:** Agent runs as always-on service on Pi.

---

## 6. Blockers

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| OpenClaw gateway not installed | Cannot receive WhatsApp messages | Install and configure OpenClaw first |
| WhatsApp not linked | Cannot test message flow | QR login during Phase 1 |
| NIM API key not configured | Cannot classify messages | Use mock LLM for testing |
| Docker not installed | Cannot containerize | Use systemd directly on Pi |
| No Pi hardware yet | Cannot test production | Laptop-first development |

---

## 7. Testing Strategy

### Unit Tests

- State machine transitions (all paths)
- Policy engine decisions
- Context resolution
- Task creation/resolution
- Command parsing
- Configuration loading

### Integration Tests

- Mock WhatsApp provider → full message flow
- Mock LLM provider → classification pipeline
- Database persistence across restart
- Timer reconstruction

### Test Mocks

```python
MockWhatsAppProvider  # simulate messages, read states, replies
MockLLMProvider       # deterministic classification responses
```

### Test Commands

```bash
pytest tests/ -v
pytest tests/ --cov=src/agent --cov-report=term-missing
ruff check src/ tests/
mypy src/
```

---

## 8. Next Steps

1. Initialize git repository
2. Create `pyproject.toml` with dependencies
3. Create `.env.example` and `.gitignore`
4. Create basic project structure
5. Implement config loading
6. Implement SQLite database
7. Implement application entrypoint
8. Verify `python -m agent` starts

**Ready to begin Phase 0.**
