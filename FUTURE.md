# FUTURE

Longer-term ideas.

## Features
- Multi-language support (translate/classify) if used internationally.
- Media processing: extract text from images/docs if media_reference appears (OCR/transcribe). Current code filters media-only messages as not relevant unless text present.
- Priority inbox in self-chat: summarize open tasks by urgency.
- Calendar integration for meeting requests (create reminders/links).
- Voice notes transcription via OpenClaw/transcriber hook if available.

## Architecture
- Read receipts: if bridge can emit read events reliably, enable read-unanswered path fully (currently gated by availability). Consider Baileys message receipt tap in bridge plugin.
- Group auto-replies: currently disabled by policy (groups never auto-ack). Could allow opt-in with strict allowlist and rate limits.
- Memory/RAG: memory.enabled is false; could extract facts from long conversations and inject into context.
- Distributed state: SQLite is fine for single device; consider migration path to Postgres if scaling.

## Observability
- Structured event export (JSONL) for analysis outside DB.
- Tracing per message_id across monitoring states (timeline view).
- Alerting on LLM failures, bridge unreachable, watchdog stuck.

## Security/ops
- Secrets rotation via env only (no config). 
- Mask PII in logs (currently structlog + selective fields).
- Rate-limit self-chat commands to avoid abuse.

## Bridge improvements
- Cache channel runtime across sends to reduce latency.
- Expose get_read_state via bridge if underlying API exists; otherwise document async-only.
- Add plugin config validation and better error messages.