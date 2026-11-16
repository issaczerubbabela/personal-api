"""SQLite store: one `records` table shared by every source, plus full-text search."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,              -- github, calendar, expenses, ...
    type        TEXT NOT NULL,              -- repo, event, expense, ...
    external_id TEXT NOT NULL,              -- stable id within (source, type)
    timestamp   TEXT NOT NULL,              -- UTC, YYYY-MM-DDTHH:MM:SSZ
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    url         TEXT,
    data        TEXT NOT NULL DEFAULT '{}', -- source-specific JSON
    synced_at   TEXT NOT NULL,
    UNIQUE (source, type, external_id)
);
CREATE INDEX IF NOT EXISTS records_timestamp ON records (timestamp);
CREATE INDEX IF NOT EXISTS records_source_type ON records (source, type, timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
    USING fts5 (title, content, content='records', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts (rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts (records_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts (records_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO records_fts (rowid, title, content) VALUES (new.id, new.title, new.content);
END;

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    records     INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
"""


@dataclass
class Record:
    source: str
    type: str
    external_id: str
    timestamp: str
    title: str = ""
    content: str = ""
    url: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def to_utc(value: datetime | date | str) -> str:
    """Normalise a date/datetime/ISO string to 'YYYY-MM-DDTHH:MM:SSZ' (sorts lexically)."""
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        value = datetime.fromisoformat(text)
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return to_utc(datetime.now(timezone.utc))


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, records: Iterable[Record]) -> int:
    synced_at = now_utc()
    rows = [
        (r.source, r.type, r.external_id, r.timestamp, r.title, r.content, r.url,
         json.dumps(r.data, default=str), synced_at)
        for r in records
    ]
    conn.executemany(
        """INSERT INTO records
               (source, type, external_id, timestamp, title, content, url, data, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (source, type, external_id) DO UPDATE SET
               timestamp = excluded.timestamp, title = excluded.title,
               content = excluded.content, url = excluded.url,
               data = excluded.data, synced_at = excluded.synced_at""",
        rows,
    )
    return len(rows)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out
