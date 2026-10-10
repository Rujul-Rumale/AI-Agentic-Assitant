/**
 * WhatsApp Agent Bridge — OpenClaw plugin.
 *
 * Fast-and-dirty first working version:
 *  - `message_received` hook -> POST to the Python agent loopback (127.0.0.1:8090/events).
 *  - `before_dispatch` claims all WhatsApp inbound so OpenClaw's own agent never
 *    auto-replies (only the Python policy engine decides responses).
 *  - local HTTP server on 127.0.0.1:<bridgePort> (default 18788) exposing /wa/* routes
 *    that the Python `OpenClawBridgeProvider` calls.
 *
 * VERIFY: the exact WhatsApp send call shape below (`api.runtime.channel.whatsapp
 * .sendMessageWhatsApp`); if the gateway version changes the surface, this is the
 * single file to adjust.
 */
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";

const DEFAULT_PYTHON_URL = "http://127.0.0.1:8090/events";
const DEFAULT_BRIDGE_PORT = 18788;

// The WhatsApp channel publishes its runtime to a GLOBAL, pluginId-keyed slot.
// Creating the same store here shares that slot, so we can call the channel's
// real outbound send method directly (lazily, after the channel initializes).
const waRuntime = createPluginRuntimeStore<any>({
  pluginId: "whatsapp",
  errorMessage: "WhatsApp runtime not initialized",
});
const waChannelStore = createPluginRuntimeStore<any>({
  key: "plugin-runtime:whatsapp:channel-context-owner",
  errorMessage: "WhatsApp channel runtime not initialized",
});

// Module-level handle: OpenClaw may register a plugin more than once per process
// (discovery + startup); never bind the HTTP port twice.
let httpServer: Server | null = null;
// Plugin `api` captured at register time; the WhatsApp channel runtime is only
// populated after channel startup, so resolve it lazily at each HTTP call.
let apiRef: any = null;
let dumped = false;

function dumpRuntime(): void {
  if (dumped) return;
  const rt = apiRef?.runtime;
  const wrt = waRuntime.tryGetRuntime();
  const wch = waChannelStore.tryGetRuntime();
  if (!rt && !wrt && !wch) return;
  dumped = true;
  try {
    console.error("[bridge-debug] wa-runtime present:", !!wrt, "channel-owner present:", !!wch);
    const snap = (o: any) => (o && typeof o === "object" ? Object.keys(o).join(",") : `type=${typeof o}`);
    if (wrt && typeof wrt === "object") {
      console.error(`[bridge-debug] wa-runtime keys=${snap(wrt)}`);
      const c = wrt.channel;
      if (c && typeof c === "object") {
        console.error(`[bridge-debug] wa-runtime.channel keys=${snap(c)}`);
        const ob = c.outbound;
        if (ob) {
          console.error(`[bridge-debug] wa-runtime.channel.outbound keys=${snap(ob)}`);
          console.error(`[bridge-debug] loadAdapter typeof=${typeof ob.loadAdapter}`);
        }
      }
    }
    if (wch && typeof wch === "object") {
      console.error(`[bridge-debug] channel-owner keys=${snap(wch)}`);
    }
    if (!rt) return;
    console.error("[bridge-debug] runtime keys:", snap(rt));
    const ch = rt?.channel;
    console.error("[bridge-debug] channel keys:", ch ? snap(ch) : "none");
  } catch (e) {
    console.error("[bridge-debug] dump failed", String(e));
  }
}

async function resolveRuntimeConfig(): Promise<any> {
  const cfgApi = (apiRef?.runtime?.config ?? {}) as any;
  const current = cfgApi.current;
  if (typeof current === "function") {
    try {
      return await current();
    } catch (e) {
      console.error("[bridge-debug] config.current() failed", String(e));
      return undefined;
    }
  }
  return current;
}

function chanSendTarget(chan: any): ((chatId: string, text: string, opts: any) => Promise<any>) | null {
  if (!chan || typeof chan !== "object") return null;
  if (typeof chan.sendMessageWhatsApp === "function") {
    return (chatId, text, opts) => chan.sendMessageWhatsApp(chatId, text, opts ?? {});
  }
  if (typeof chan.sendText === "function") {
    return async (chatId, text, opts) => {
      const payload: Record<string, unknown> = { to: chatId, text };
      const cfg = await resolveRuntimeConfig();
      if (cfg && typeof cfg === "object") payload.cfg = cfg;
      if (opts?.quotedMessageKey?.id) payload.replyToId = opts.quotedMessageKey.id;
      const result = await chan.sendText(payload);
      return (result as any)?.receipt ?? result ?? {};
    };
  }
  if (typeof chan.sendPayload === "function") {
    return async (chatId, text, _opts) => {
      const ctx: Record<string, unknown> = { channel: "whatsapp", payload: { text }, to: chatId };
      return await chan.sendPayload(ctx);
    };
  }
  return null;
}

async function resolveChan(): Promise<any> {
  dumpRuntime();
  for (const cand of [waChannelStore.tryGetRuntime(), waRuntime.tryGetRuntime()]) {
    if (cand && chanSendTarget(cand)) return cand;
  }
  const ob = apiRef?.runtime?.channel?.outbound;
  if (ob && typeof ob.loadAdapter === "function") {
    try {
      const adapter = await ob.loadAdapter("whatsapp");
      if (adapter && chanSendTarget(adapter)) return adapter;
    } catch (e) {
      console.error("[bridge-debug] loadAdapter failed", String(e));
    }
  }
  const direct = apiRef?.runtime?.channel?.whatsapp;
  if (direct && direct !== true) return direct;
  if ((httpServer as any)?.__chan) return (httpServer as any).__chan;
  return null;
}

function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf-8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(new Error(`bad json: ${String(err)}`));
      }
    });
    req.on("error", reject);
  });
}

function send(res: ServerResponse, code: number, obj: unknown): void {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "content-type": "application/json" });
  res.end(body);
}

function ensureServer(port: number): Server {
  if (httpServer) return httpServer;
  const server = createServer(async (req, res) => {
    const base = `http://127.0.0.1:${port}`;
    const url = new URL(req.url ?? "/", base);
    const matched = url.pathname.match(/^\/wa\/([^/]+)/);
    if (!matched) {
      send(res, 404, { ok: false, error: "not_found", hint: "use /wa/__ping" });
      return;
    }
    const route = matched[1];
    const method = req.method ?? "GET";

    if (method === "GET" && route === "__ping") {
      send(res, 200, { ok: true, service: "whatsapp-agent-bridge", ts: new Date().toISOString() });
      return;
    }
    if (method === "POST" && route === "ping") {
      send(res, 200, { ok: true, service: "whatsapp-agent-bridge", ts: new Date().toISOString() });
      return;
    }
    if (method === "GET" && route === "debug") {
      const chan = await resolveChan();
      const cfg = await resolveRuntimeConfig();
      const snap2 = (o: any) => (o && typeof o === "object" ? Object.keys(o).join(",") : `type=${typeof o}`);
      const probe: Record<string, unknown> = {
        runtime_present: !!apiRef?.runtime,
        wa_runtime: waRuntime.tryGetRuntime() ? "yes" : "no",
        channel_owner: waChannelStore.tryGetRuntime() ? "yes" : "no",
        loadAdapter_type: typeof apiRef?.runtime?.channel?.outbound?.loadAdapter,
        config_resolved: cfg ? "yes" : "no",
        config_keys: cfg && typeof cfg === "object" ? snap2(cfg) : null,
        config_whatsapp_keys: (cfg as any)?.whatsapp ? snap2((cfg as any).whatsapp) : null,
        config_accounts: Array.isArray((cfg as any)?.whatsapp?.accounts) ? (cfg as any).whatsapp.accounts.length : null,
        adapter_sort_path: chan ? Object.getOwnPropertyNames(chan).join(",") : null,
        send_capable: !!chanSendTarget(chan),
      };
      send(res, 200, { ok: true, ...probe });
      return;
    }

    if (method === "POST" && route === "send") {
      let body: Record<string, unknown>;
      try {
        body = await readBody(req);
      } catch (err) {
        send(res, 400, { ok: false, error: String(err) });
        return;
      }
      const chan = await resolveChan();
      const doSend = chanSendTarget(chan);
      if (!doSend) {
        send(res, 502, {
          ok: false,
          error: "whatsapp channel runtime unavailable (no send surface exposed)",
        });
        return;
      }
      try {
        let result: any;
        for (let attempt = 0; attempt < 2; attempt++) {
          result = await doSend(
            body.chat_id,
            body.text,
            body.message_id
              ? { quotedMessageKey: { id: body.message_id } }
              : undefined,
          );
          const id = result?.messageId ?? result?.id ?? result?.["message_id"];
          if (id) break;
          await new Promise((r) => setTimeout(r, 500));
        }
        const id = result?.messageId ?? result?.id ?? result?.["message_id"] ?? null;
        send(res, 200, { ok: true, message_id: id, result: result ?? null });
      } catch (err) {
        send(res, 502, { ok: false, error: String(err) });
      }
      return;
    }

    if (method === "POST" && ["chat_metadata", "messages", "message"].includes(route)) {
      let body: Record<string, unknown>;
      try {
        body = await readBody(req);
      } catch (err) {
        send(res, 400, { ok: false, error: String(err) });
        return;
      }
      const chan = (await resolveChan()) as any;
      try {
        let out: unknown = {};
        if (route === "chat_metadata") {
          out = chan?.getChatMetadata
            ? await chan.getChatMetadata({ chatId: body.chat_id })
            : { chat_id: body.chat_id };
        } else if (route === "messages") {
          out = chan?.getChatMessages
            ? await chan.getChatMessages({ chatId: body.chat_id, limit: body.limit ?? 20 })
            : { messages: [] };
        } else {
          out = chan?.getChatMessage
            ? await chan.getChatMessage({ id: body.message_id })
            : { message: null };
        }
        send(res, 200, out ?? {});
      } catch (err) {
        send(res, 502, { ok: false, error: String(err) });
      }
      return;
    }

    send(res, 404, { ok: false, error: "not_found", route, method });
  });

  server.on("error", (err) => {
    // Do not let a second registration or transient bind failure kill the gateway.
    console.error("[whatsapp-agent-bridge] server error", err);
  });
  server.listen(port, "127.0.0.1");
  httpServer = server;
  return server;
}

/// Resolve the WhatsApp channel runtime lazily (available after channel startup).
function waChannel(api: any): any {
  let target: any = api?.runtime?.channel?.whatsapp;
  if (!target || target === true) {
    target = (httpServer as any)?.__chan;
  }
  if (!target || target === true) {
    target = {
      async sendMessageWhatsApp(_to: string, _text: string, _opts?: any) {
        throw new Error("whatsapp channel runtime unavailable");
      },
    };
  }
  return target;
}

export default definePluginEntry({
  id: "whatsapp-agent-bridge",
  name: "WhatsApp Agent Bridge",
  description: "Bridges inbound WhatsApp events to the Python agent and exposes /wa/send.",
  register(api) {
    apiRef = api;
    const cfg = (api.pluginConfig ?? {}) as Record<string, unknown>;
    const pythonUrl = (cfg.pythonUrl as string | undefined) ?? DEFAULT_PYTHON_URL;
    const port = (cfg.bridgePort as number | undefined) ?? DEFAULT_BRIDGE_PORT;

    const server = ensureServer(port);

    // Claim all WhatsApp inbound messages so OpenClaw's own agent never auto-replies;
    // only the Python policy engine decides responses (fail-closed design).
    api.on("before_dispatch", (event: any) => {
      const channel = String(event?.channel ?? "");
      const sessionKey = String(event?.sessionKey ?? "");
      const senderId = String(event?.senderId ?? "");
      const jid = /@(c\.us|g\.us|s\.whatsapp\.net)$/;
      const isWhatsApp =
        /whatsapp/i.test(channel) ||
        /whatsapp/i.test(sessionKey) ||
        jid.test(senderId) ||
        jid.test(sessionKey);
      if (isWhatsApp) {
        return { handled: true };
      }
      return undefined;
    });

    api.on("message_received", (event: any) => {
      if (!event) return;
      const data = {
        message_id: event.messageId ?? event.id ?? "",
        chat_id: String(event.threadId ?? event.chatId ?? event.from ?? ""),
        sender_id: String(event.senderId ?? event.from ?? ""),
        timestamp: toIsoTs(event.timestamp ?? event.createdAt),
        is_outgoing: !!event.isOutgoing,
        agent_sent: !!event.agentSent,
        message_type: String(event.messageType ?? event.type ?? "TEXT").toUpperCase(),
        text: typeof event.content === "string" ? event.content : null,
        media_reference: Array.isArray(event.media) && event.media.length ? `media:${event.media.length}` : null,
        reply_to_message_id: event.replyToId ?? event.replyToIdFull ?? null,
        quoted_message_id: event.replyToIdFull ?? null,
        chat_name: event.threadName ?? null,
        sender_name: event.senderName ?? null,
      };
      void forward(pythonUrl, data);
    });

    api.on("gateway_stop", () => {
      if (httpServer) {
        httpServer.close();
        httpServer = null;
      }
    });
  },
});

function toIsoTs(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number") {
    return new Date(value).toISOString();
  }
  const s = String(value).trim();
  return s === "" ? null : s;
}

async function forward(url: string, data: Record<string, unknown>): Promise<void> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type: "message", data }),
    });
    if (!res.ok) {
      console.error(`[whatsapp-agent-bridge] forward failed status=${res.status}`);
    }
  } catch (err) {
    console.error("[whatsapp-agent-bridge] forward error", err);
  }
}