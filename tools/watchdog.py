"""Overnight watchdog: keep the Python agent and OpenClaw gateway alive.

Polls health endpoints; restarts dead services with per-service cooldowns and
daily restart caps. Pure stdlib, Windows-friendly (services started with
CREATE_NEW_PROCESS_GROUP via cmd/python shims).
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

POLL_INTERVAL_SECONDS = 60
PER_SERVICE_COOLDOWN_SECONDS = 90
MAX_CONSECUTIVE_RESTARTS = 10
DAILY_RESTART_CAP = 20
HEALTH_TIMEOUT_SECONDS = 5

AGENT_WORKDIR = r"C:\Projects\Agent"
GATEWAY_WORKDIR = r"C:\Projects\Agent"
AGENT_LOG = r"C:\Users\rujul\AppData\Local\Temp\opencode\agent.log"
AGENT_ERR = r"C:\Users\rujul\AppData\Local\Temp\opencode\agent.err"
GW_LOG = r"C:\Users\rujul\AppData\Local\Temp\opencode\gw.log"
GW_ERR = r"C:\Users\rujul\AppData\Local\Temp\opencode\gw.err"
WATCHDOG_LOG = r"C:\Users\rujul\AppData\Local\Temp\opencode\watchdog.log"

AGENT_HEALTH_URL = "http://127.0.0.1:8090/healthz"
GW_HEALTH_URL = "http://127.0.0.1:18788/wa/ping"
OPENCLAW_SHIM = r"C:\Users\rujul\AppData\Roaming\npm\openclaw.cmd"

WIN_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def log(line: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    entry = f"{ts} {line}"
    with open(WATCHDOG_LOG, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
    print(entry)


def http_get(url: str, data: bytes | None = None) -> bool:
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if data is None:  # GET health check: any 200 counts
                return True
            return "ok" in body and "true" in body
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def _append_log(path: str) -> object:
    return open(path, "a", encoding="utf-8", buffering=1)


def start_agent() -> None:
    log("watchdog restarting python agent")
    out = _append_log(AGENT_LOG)
    err = _append_log(AGENT_ERR)
    subprocess.Popen(
        [sys.executable, "-m", "agent"],
        cwd=AGENT_WORKDIR,
        env=os.environ.copy(),
        stdout=out,
        stderr=err,
        creationflags=WIN_FLAGS,
    )


def start_gateway() -> None:
    log("watchdog restarting openclaw gateway")
    out = _append_log(GW_LOG)
    err = _append_log(GW_ERR)
    subprocess.Popen(
        ["cmd", "/c", OPENCLAW_SHIM, "gateway", "run"],
        cwd=GATEWAY_WORKDIR,
        env=os.environ.copy(),
        stdout=out,
        stderr=err,
        creationflags=WIN_FLAGS,
    )


def main() -> None:
    log("watchdog started")
    services = {
        "agent": {"health": lambda: http_get(AGENT_HEALTH_URL), "start": start_agent},
        "gateway": {"health": lambda: http_get(GW_HEALTH_URL, b"{}"), "start": start_gateway},
    }
    state = {
        name: {"restarts": 0, "consecutive": 0, "last_restart": 0.0, "skip_until": 0.0, "up": False}
        for name in services
    }
    day = datetime.date.today()

    try:
        while True:
            today = datetime.date.today()
            if today != day:
                day = today
                for s in state.values():
                    s["restarts"] = 0

            for name, cfg in services.items():
                st = state[name]
                if time.time() < st["skip_until"]:
                    continue
                up = cfg["health"]()
                if up:
                    if not st["up"]:
                        log(f"{name} is UP")
                        st["consecutive"] = 0
                    st["up"] = True
                    continue

                st["up"] = False
                if st["restarts"] >= DAILY_RESTART_CAP:
                    log(f"FATAL {name}: daily restart cap reached; giving up for today")
                    st["skip_until"] = time.time() + 3600
                    continue

                now = time.time()
                if now - st["last_restart"] < PER_SERVICE_COOLDOWN_SECONDS:
                    continue
                cfg["start"]()
                st["restarts"] += 1
                st["consecutive"] += 1
                st["last_restart"] = now
                st["skip_until"] = now + PER_SERVICE_COOLDOWN_SECONDS
                log(f"{name} DOWN -> restart #{st['restarts']} (consecutive={st['consecutive']})")
                if st["consecutive"] >= MAX_CONSECUTIVE_RESTARTS:
                    log(f"STUCK {name}: {st['consecutive']} consecutive failures; pausing 10min (check gateway.config / relink)")
                    st["skip_until"] = now + 600
                    st["consecutive"] = 0

            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log("watchdog stopped (SIGINT)")
    except Exception as exc:  # pragma: no cover - watchdog must never die silently
        log(f"watchdog crashed: {exc}")
        raise


if __name__ == "__main__":
    main()