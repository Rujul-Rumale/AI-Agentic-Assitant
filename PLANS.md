# PLANS

Immediate/next priorities based on CURRENT_STATE.

## 1. Harden WhatsApp bridge reliability
- Add heartbeat logging and resilient retries for POST to Python (backpressure on 5xx).
- Add /wa/health endpoint parity and expose send_capable probe more explicitly.
- Consider reconnection strategy if channel runtime becomes unavailable between sends (retry loadAdapter with small backoff).

## 2. Monitoring & timing
- Verify read-event delivery end-to-end with real WhatsApp (bridge -> inbound -> MessageRead). If read events never arrive, either implement bridge tap for receipts (Baileys messages.update) or document the limitation; keep read-unanswered dormant by config if needed.
- Tune thresholds: unread 1m (aggressive) — validate in production; read-unanswered 60m reasonable.
- Add metrics counters (acks sent, acks denied by reason, tasks created/resolved) to events/audit.

## 3. Policy/acks
- Keep dry_run false in config (current). can_notify_self() respects this.
- Add per-chat rate-limit tuning if needed (2/day + 60m conservative).
- Test acknowledgement text generation with NIM for variety and avoid loops.

## 4. Self-chat robustness
- Improve self-send dedup (currently recent text list) — use message-id/hash with TTL to reduce false positives across restarts.
- Add self-chat ID validation at startup (warn if empty/malformed).

## 5. Operational
- Add watchdog alerting on repeated STUCK state (consecutive failures >=10).
- Add a simple healthcheck script/command to verify all three components.
- Document backup/rotation for logs in %TEMP%\opencode\*.log.

## 6. Testing & CI
- Integration test for bridge ingest path (message_received -> events) end-to-end with FastAPI app.
- Test dry_run transition behavior (policy decisions) including notify_self(force).
- Regression tests for read-unanswered path when MessageRead arrives late (race with unread timer).

## 7. Deployment cleanup
- Keep .env/gitignored; do not commit data/*.db*.
- Ensure pyproject dependencies align with runtime. Optional openclaw-sdk not needed (bridge uses HTTP).
- On Pi later: systemd service wrapping watchdog or direct start; ensure env loaded.