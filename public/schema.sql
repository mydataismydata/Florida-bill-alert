-- One table for people, one for what has been sent, one for requests.
-- Portable between MySQL and SQLite on purpose: the difference between them
-- should never be the reason a subscription breaks.

CREATE TABLE IF NOT EXISTS subscriber (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT    DEFAULT '',
    role          TEXT    DEFAULT '',          -- legislator_staff | citizen | ''
    county        TEXT    DEFAULT '',
    areas         TEXT    DEFAULT '',          -- comma-separated slugs from the fixed 13
    daily         INTEGER NOT NULL DEFAULT 1,
    weekly        INTEGER NOT NULL DEFAULT 1,
    confirm_token TEXT    DEFAULT '',          -- single use, cleared on confirm
    confirm_sent  TEXT    DEFAULT '',
    confirmed_at  TEXT    DEFAULT '',
    unsubscribed  TEXT    DEFAULT '',
    created_at    TEXT    NOT NULL,
    bounces       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sub_confirmed ON subscriber (confirmed_at);

-- What went to whom. Cheap insurance against a cron that fires twice.
CREATE TABLE IF NOT EXISTS sent (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER NOT NULL,
    payload       TEXT    NOT NULL,            -- the digest filename
    sent_at       TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_once ON sent (subscriber_id, payload);

-- "Analyse this bill" from a bill page. The analysis box pulls these, and
-- here ever reaches inward on its own.
CREATE TABLE IF NOT EXISTS request (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session    TEXT NOT NULL,
    num        INTEGER NOT NULL,
    email      TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    fulfilled  TEXT DEFAULT ''
);
