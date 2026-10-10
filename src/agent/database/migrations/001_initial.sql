-- 001: initial schema

CREATE TABLE IF NOT EXISTS chats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          TEXT NOT NULL UNIQUE,
    chat_type        TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    participant_name TEXT NOT NULL DEFAULT '',
    last_message_at  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id           TEXT NOT NULL UNIQUE,
    chat_id              TEXT NOT NULL,
    sender_id            TEXT NOT NULL,
    timestamp            TEXT,
    received_at          TEXT,
    read_at              TEXT,
    is_read              INTEGER NOT NULL DEFAULT 0,
    is_outgoing          INTEGER NOT NULL DEFAULT 0,
    agent_sent           INTEGER NOT NULL DEFAULT 0,
    chat_type            TEXT NOT NULL,
    message_type         TEXT NOT NULL DEFAULT 'TEXT',
    text                 TEXT,
    media_reference      TEXT,
    reply_to_message_id  TEXT,
    quoted_message_id    TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages (chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages (sender_id);

CREATE TABLE IF NOT EXISTS tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL UNIQUE,
    chat_id            TEXT NOT NULL,
    source_message_id  TEXT,
    sender_id          TEXT NOT NULL DEFAULT '',
    intent             TEXT NOT NULL,
    summary            TEXT NOT NULL DEFAULT '',
    requested_action   TEXT NOT NULL DEFAULT '',
    urgency            TEXT NOT NULL DEFAULT 'NORMAL',
    deadline           TEXT,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    ack_unread_sent    INTEGER NOT NULL DEFAULT 0,
    ack_read_sent      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    resolved_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_chat_status ON tasks (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);

CREATE TABLE IF NOT EXISTS monitoring (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id                  TEXT NOT NULL UNIQUE,
    state                       TEXT NOT NULL,
    unread_check_at             TEXT,
    read_unanswered_check_at    TEXT,
    ack_unread_sent             INTEGER NOT NULL DEFAULT 0,
    ack_read_sent               INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitoring_due
    ON monitoring (unread_check_at, read_unanswered_check_at);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL,
    event_type TEXT NOT NULL,
    chat_id    TEXT,
    message_id TEXT,
    task_id    TEXT,
    details    TEXT,
    result     TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_chat ON events (chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_task ON events (task_id);

CREATE TABLE IF NOT EXISTS acknowledgements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    task_id    TEXT,
    chat_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    body       TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    dry_run    INTEGER NOT NULL DEFAULT 0,
    policy     TEXT,
    result     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ack_chat_time ON acknowledgements (chat_id, sent_at);

CREATE TABLE IF NOT EXISTS memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,
    attribute  TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (entity, attribute)
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);