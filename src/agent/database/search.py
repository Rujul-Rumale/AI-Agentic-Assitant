"""Cross-chat message search over the local message store (spec extension).

When the user asks "search X" or "what did Y say about Z" in self-chat,
this module queries the local DB for matching messages across all chats
and returns formatted results.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.database.db import Database

# Words dropped when deriving search terms from a natural-language query.
_STOPWORDS = {
    "and", "for", "about", "from", "regarding", "containing", "any",
    "some", "with", "what", "who", "did", "does", "said", "sent", "say",
    "tell", "told", "message", "messages", "chat", "chats", "search",
    "find", "look", "regarding", "the", "my", "you", "me", "that", "this",
    "someone", "anyone", "there", "are", "in", "on", "over", "an", "a",
}


def _terms_from_query(query: str) -> list[str]:
    terms = [t for t in re.split(r"\W+", query.lower()) if t and t not in _STOPWORDS]
    return terms[:4]


async def search_messages(
    db: Database,
    query: str,
    *,
    limit: int = 10,
    chat_id: str | None = None,
    exclude_chat_id: str | None = None,
    match_all: bool = False,
) -> list[dict]:
    """Search messages by text content across chats.

    Terms are ANDed when ``match_all`` (strict) or ORed (default) — OR mode is
    best for name/topic searches ("what did Ram say about project"). Results
    are ordered by number of matched terms (desc) then timestamp (desc).
    """
    terms = _terms_from_query(query)
    if not terms:
        return []

    like_conds = []
    for term in terms:
        like_conds.append("m.text LIKE ?")

    if match_all:
        where = " AND ".join(like_conds)
    else:
        where = " OR ".join(like_conds)

    rank_expr = " + ".join("(COALESCE(m.text,'') LIKE ?)" for _ in terms)

    clauses = [f"({where})", "m.is_outgoing = 0"]
    if exclude_chat_id:
        clauses.append("m.chat_id != ?")
    if chat_id:
        clauses.append("m.chat_id = ?")

    # Bind order == placeholder appearance order in the SQL text: rank expr
    # placeholders come first (they appear in the SELECT list), then WHERE.
    params: list = [f"%{t}%" for t in terms]  # rank
    params.extend(f"%{t}%" for t in terms)  # where
    if exclude_chat_id:
        params.append(exclude_chat_id)
    if chat_id:
        params.append(chat_id)
    params.append(limit)

    sql = (
        "SELECT m.chat_id, c.name AS chat_name, m.sender_id, "
        "c.participant_name AS sender_name, m.text, m.timestamp, m.message_id, "
        f"({rank_expr}) AS match_count "
        "FROM messages m LEFT JOIN chats c ON m.chat_id = c.chat_id "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY match_count DESC, m.timestamp DESC "
        "LIMIT ?"
    )

    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows]


def format_search_results(results: list[dict], query: str, *, max_entries: int = 10) -> str:
    """Format search results into a readable text response."""
    if not results:
        return f"No messages found matching '{query}'."

    lines = [f"Found {len(results)} message(s) matching '{query}':\n"]
    for i, r in enumerate(results[:max_entries], 1):
        chat_label = r.get("chat_name") or r.get("chat_id", "unknown")
        sender = r.get("sender_name") or r.get("sender_id", "unknown")
        text = (r.get("text") or "").strip().replace("\n", " ")
        timestamp = (r.get("timestamp") or "")[:16].replace("T", " ")

        if len(text) > 120:
            text = text[:117] + "..."

        lines.append(f"{i}. [{chat_label}] {sender} ({timestamp}):\n   {text}\n")

    lines.append(f"({len(results)} total; showing top {min(len(results), max_entries)})")
    return "\n".join(lines)


def extract_search_query(text: str) -> str | None:
    """Detect search intent in self-chat messages and extract the query.

    Returns the search query string if detected (original case preserved),
    None otherwise.
    """
    original = text.strip()
    text_lower = original.lower()
    if len(text_lower) < 4:
        return None

    patterns = [
        r"^(?:search|find|look\s*(?:up|for))\s+(?:for\s+)?(.+)$",
        r"^(?:show|fetch|get)\s+(?:me\s+)?(?:messages?\s+(?:about|from|containing)\s+)?(.+)$",
        r"^(?:any|are\s+there)\s+(?:messages?\s+)?(?:about|from|containing|regarding)\s+(.+)$",
        r"^(?:what\s+did|who\s+(?:said|sent))\s+(.+)$",
        r"^(?:check|scan)\s+(?:my\s+)?(?:chats?\s+(?:for|about)\s+)?(.+)$",
    ]

    for pattern in patterns:
        m = re.match(pattern, original, re.IGNORECASE)
        if m:
            query = m.group(1).strip()
            for prefix in ("for ", "about ", "from ", "regarding ", "containing "):
                if query.lower().startswith(prefix):
                    query = query[len(prefix):]
            return query.strip() if query.strip() else None

    return None