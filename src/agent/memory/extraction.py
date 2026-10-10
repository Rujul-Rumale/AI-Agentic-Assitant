"""Memory extraction (spec §78-79).

Deliberately a no-op for v1: extraction ships only after the core monitoring
and task system is proven stable. The interface exists so the pipeline does not
change later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.context.resolver import ResolvedContext


async def extract(context: ResolvedContext) -> list[tuple[str, str, str]]:
    """Return [(entity, attribute, value), ...] candidates from a context.

    v1 returns nothing. Future versions may extract people/projects/preferences.
    """
    return []