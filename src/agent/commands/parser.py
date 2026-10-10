"""Command parser (spec §56)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ALIASES: dict[str, str] = {
    "tasks": "pending",
    "open": "pending",
    "quit": "pause",
    "resume": "resume",
}


@dataclass
class Command:
    name: str
    args: tuple[str, ...] = ()

    @staticmethod
    def from_text(text: str) -> Command | None:
        """Parse slash command or natural language stub."""
        text = text.strip()
        m = re.match(r"^/(\w+)\s*(.*)$", text)
        if m:
            raw = m.group(1).lower()
            name = _ALIASES.get(raw, raw)
            args = tuple(a.strip() for a in m.group(2).split() if a.strip())
            return Command(name, args)
        low = text.lower()
        if low in ("status", "tasks", "open", "resume", "pause", "help"):
            return Command(_ALIASES.get(low, low))
        # NL stubs (preserve case on ids)
        m = re.match(r"mark\s+task\s+(\S+)\s+(done|dismiss)", text, re.IGNORECASE)
        if m:
            return Command("resolve", (m.group(1), m.group(2).lower()))
        m = re.match(r"dismiss\s+task\s+(\S+)", text, re.IGNORECASE)
        if m:
            return Command("dismiss", (m.group(1),))
        return None