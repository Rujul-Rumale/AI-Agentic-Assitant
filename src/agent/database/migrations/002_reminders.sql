-- 002: self-chat reminders & scheduled messages

CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id   TEXT NOT NULL UNIQUE,
    chat_id       TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'REMINDER',   -- REMINDER | SCHEDULED_MESSAGE
    text          TEXT NOT NULL,
    due_at        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING',    -- PENDING | FIRED | CANCELLED
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT '',
    fired_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, due_at);