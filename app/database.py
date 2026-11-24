import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .identity import USER_ID

DATA_DIR = Path(os.getenv("SDR_DATA_DIR", "data")).resolve()


def data_dir():
    uid = USER_ID.get()
    if uid is None:
        return DATA_DIR
    if type(uid) is not int or uid < 1:
        raise ValueError('Invalid account id')
    return DATA_DIR / 'users' / str(uid)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(directory / "lume.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_exclusions (kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY, domain TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'new',
            data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            delivered_day TEXT, attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id)
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, lead_id INTEGER REFERENCES leads(id),
            at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS lead_status ON leads(status);
        CREATE INDEX IF NOT EXISTS lead_day ON leads(delivered_day);
        """)
        db.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (now(),))


def get_setting(key, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with connect() as db:
        db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def event(db, lead_id, action, detail):
    db.execute("INSERT INTO events(lead_id,at,action,detail) VALUES(?,?,?,?)", (lead_id, now(), action, detail))


def unpack(row):
    return {**dict(row), "data": json.loads(row["data"])}
