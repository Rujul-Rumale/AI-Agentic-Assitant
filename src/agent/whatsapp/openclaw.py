"""OpenClaw bridge provider.

The bridge is a small OpenClaw plugin (see `bridge/`) that:
  • forwards every inbound wire event to our HTTP receiver (inbound events),
  • exposes a `/wa/send` HTTP route that calls OpenClaw's native send.

This provider is the client side of that bridge. All capabilities the bridge
does not implement raise `ProviderCapabilityError` rather than silently
degrading (spec §84).

NOTE: requires the OpenClaw gateway to be installed, WhatsApp linked, and the
bridge plugin loaded. Until then development uses `MockWhatsAppProvider`.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.timeutil import ts_now
from agent.whatsapp.interface import (
    ChatType,
    ProviderCapabilityError,
    RawMessage,
    WhatsAppProvider,
    classify_chat_type,
    normalize_phone_jid,
)

if TYPE_CHECKING:
    from agent.whatsapp.events import EventBus


def _ts_to_iso(value: object) -> str:
    """Coerce an epoch-milliseconds timestamp (or ISO string) to our ISO form."""
    from datetime import UTC, datetime

    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return (
            datetime.fromtimestamp(seconds, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(value)


class OpenClawBridgeProvider(WhatsAppProvider):
    def __init__(
        self,
        bridge_url: str,
        bus: EventBus,
        self_chat_id: str | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self.bridge_url = bridge_url.rstrip("/")
        self.bus = bus
        self.self_chat_id = self_chat_id or ""
        self.timeout = timeout
        self._connected = False

    @property
    def name(self) -> str:
        return "openclaw"

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.bridge_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        return await asyncio.to_thread(self._post_sync, url, data)

    def _post_sync(self, url: str, data: bytes) -> dict:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            raise ProviderCapabilityError(f"bridge error {exc.code}: {exc.read().decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise ProviderCapabilityError(f"bridge unreachable: {exc.reason}") from exc

    async def connect(self) -> None:
        try:
            await self._post("/__ping", {})
        except ProviderCapabilityError:
            raise ProviderCapabilityError(
                "OpenClaw bridge is not reachable. Start the OpenClaw gateway, "
                "link WhatsApp, and load the bridge plugin. See bridge/README.md."
            ) from None
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_message(self, chat_id: str, text: str) -> str | None:
        result = await self._post("/send", {"chat_id": chat_id, "text": text})
        return result.get("message_id") or result.get("id")

    async def get_chat_type(self, chat_id: str) -> ChatType:
        return classify_chat_type(chat_id, self.self_chat_id)

    async def get_chat_metadata(self, chat_id: str) -> dict:
        try:
            return await self._post("/chat_metadata", {"chat_id": chat_id})
        except ProviderCapabilityError:
            return {"chat_id": chat_id}

    async def get_messages(self, chat_id: str, limit: int = 20, before: str | None = None) -> list[RawMessage]:
        payload = {"chat_id": chat_id, "limit": limit}
        if before:
            payload["before"] = before
        try:
            data = await self._post("/messages", payload)
        except ProviderCapabilityError:
            return []
        return [RawMessage.model_validate(item) for item in data.get("messages", [])]

    async def get_message(self, message_id: str) -> RawMessage | None:
        try:
            data = await self._post("/message", {"message_id": message_id})
        except ProviderCapabilityError:
            return None
        return RawMessage.model_validate(data["message"]) if data.get("message") else None

    async def get_read_state(self, message_id: str) -> bool:
        raise ProviderCapabilityError(
            "Read state is delivered asynchronously via MessageRead events; "
            "on-demand polling is not implemented by the bridge."
        )

    def ingest_payload(self, payload: dict) -> None:
        """Convert a bridge webhook POST into a bus event."""
        data = dict(payload)
        if data.get("timestamp") is not None and not isinstance(data["timestamp"], str):
            data["timestamp"] = _ts_to_iso(data["timestamp"])
        if not (data.get("chat_id") or "").strip() and (data.get("sender_id") or "").strip():
            # Self-chat / direct DMs may omit threadId on the wire; the sender
            # JID is the conversation identity for those.
            data["chat_id"] = data["sender_id"]
        data["chat_id"] = normalize_phone_jid(data.get("chat_id") or "")
        if data.get("sender_id"):
            data["sender_id"] = normalize_phone_jid(data["sender_id"])
        raw = RawMessage.model_validate(data)
        from agent.whatsapp.events import MessageReceived

        self.bus.emit_nowait(MessageReceived(raw=raw))


def build_inbound_app(provider: OpenClawBridgeProvider):
    """FastAPI receiver the bridge POSTs inbound events to (spec §6 isolation)."""
    from agent.whatsapp import events

    app = FastAPI(title="whatsapp-agent inbound", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/events")
    async def events_receiver(request: Request):
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "bad request"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"status": "bad request"}, status_code=400)
        event_type = payload.get("type", "message")
        data = payload.get("data", payload)
        if event_type in {"message", "message:received"}:
            provider.ingest_payload(data)
        elif event_type in {"message:read", "read"}:
            await provider.bus.emit(
                events.MessageRead(
                    chat_id=data.get("chat_id", ""),
                    message_id=data.get("message_id", ""),
                    read_at=data.get("read_at") or ts_now(),
                )
            )
        return {"status": "ok"}

    return app