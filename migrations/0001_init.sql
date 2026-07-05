-- Migration: initial schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER NOT NULL,
    added_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT UNIQUE,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    status TEXT DEFAULT 'open',
    assigned_admin_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    closed_at TEXT,
    FOREIGN KEY (assigned_admin_id) REFERENCES admins(user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_role TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_id TEXT,
    text_content TEXT,
    timestamp TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
