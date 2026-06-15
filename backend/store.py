"""SQLite read/write + cleanup for the events table."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "streetwise.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                 TEXT PRIMARY KEY,
    city               TEXT DEFAULT 'chicago',
    event_type         TEXT NOT NULL,
    location_name      TEXT,
    lat                REAL,
    lng                REAL,
    geocode_failed     INTEGER DEFAULT 0,
    summary            TEXT NOT NULL,
    impact_roads       TEXT,
    impact_transit     TEXT,
    impact_pedestrian  TEXT,
    confidence         REAL NOT NULL,
    sources            TEXT,
    estimated_duration TEXT,
    detected_at        TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    expires_at         TEXT
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the events table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute(SCHEMA)


def cleanup_old_events(conn=None):
    """Delete events older than 24 hours. Run at the start of each poll cycle."""
    sql = "DELETE FROM events WHERE detected_at < datetime('now', '-24 hours')"
    if conn is not None:
        conn.execute(sql)
        return
    with get_connection() as conn:
        conn.execute(sql)


def upsert_event(event: dict, conn=None):
    """Insert a new event or replace an existing one by id."""
    columns = [
        "id", "city", "event_type", "location_name", "lat", "lng",
        "geocode_failed", "summary", "impact_roads", "impact_transit",
        "impact_pedestrian", "confidence", "sources", "estimated_duration",
        "detected_at", "updated_at", "expires_at",
    ]
    row = dict(event)
    if isinstance(row.get("sources"), (list, tuple)):
        row["sources"] = json.dumps(row["sources"])
    row.setdefault("city", "chicago")
    row.setdefault("geocode_failed", 0)
    for column in columns:
        row.setdefault(column, None)

    placeholders = ", ".join(f":{c}" for c in columns)
    sql = f"INSERT OR REPLACE INTO events ({', '.join(columns)}) VALUES ({placeholders})"

    if conn is not None:
        conn.execute(sql, row)
        return
    with get_connection() as conn:
        conn.execute(sql, row)


def get_event(event_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def get_active_events(min_confidence: float = 0.4, event_type: str | None = None,
                       limit: int | None = None) -> list[dict]:
    """Return events with confidence >= min_confidence, optionally filtered/limited."""
    sql = "SELECT * FROM events WHERE confidence >= ?"
    params: list = [min_confidence]

    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)

    sql += " ORDER BY detected_at DESC"

    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    with get_connection() as conn:
        cleanup_old_events(conn)
    print(f"Database ready at {DB_PATH}")
