"""SQLite persistence: migrations, events, event_sources, raw_items.

Schema v2 rules (docs/architecture.md):
- Nothing is deleted. "Active" means cleared_at IS NULL — a query, not a lifecycle.
- event_sources is the source of truth for which source items feed which event;
  events has no sources column — serialization joins the table.
- raw_items archives every fetched payload before any processing can fail.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("STREETWISE_DB", _ROOT / "streetwise.db"))
MIGRATIONS_DIR = _ROOT / "migrations"

# Confidence is derived, never stored: components live in columns so display math can
# evolve without re-ingesting (freshness joins at read time in a later step).
_CONFIDENCE_SQL = "(score_source + score_extraction + score_corrob)"

EVENT_COLUMNS = [
    "id", "city", "event_type", "mode", "lines", "station", "location_name",
    "lat", "lng", "geo_kind", "severity", "scope", "verification", "summary",
    "score_source", "score_extraction", "score_corrob",
    "detected_at", "updated_at", "cleared_at",
    "first_social_at", "official_at", "latency_flagged",
]


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db():
    """Apply unapplied migrations (numbered .sql files, in order), recording each."""
    with get_connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations"
            " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (path.name, _utcnow()),
            )


def content_hash(item: dict) -> str:
    """Stable hash of a raw source item — change detection across polls."""
    canonical = json.dumps(item, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------- events

def upsert_event(event: dict, conn=None):
    """Insert or replace an event row. Unknown keys are ignored; lines may be a list."""
    row = {c: event.get(c) for c in EVENT_COLUMNS}
    if isinstance(row["lines"], (list, tuple)):
        row["lines"] = json.dumps(row["lines"])
    row["city"] = row["city"] or "chicago"
    row["lines"] = row["lines"] or "[]"
    row["geo_kind"] = row["geo_kind"] or "none"
    row["scope"] = row["scope"] or "acute"
    row["latency_flagged"] = int(row["latency_flagged"] or 0)

    placeholders = ", ".join(f":{c}" for c in EVENT_COLUMNS)
    sql = f"INSERT OR REPLACE INTO events ({', '.join(EVENT_COLUMNS)}) VALUES ({placeholders})"
    if conn is not None:
        conn.execute(sql, row)
        return
    with get_connection() as conn:
        conn.execute(sql, row)


def get_active_events(min_confidence: float = 0.4, event_type: str | None = None,
                      limit: int | None = None) -> list[dict]:
    """Active events at/above min_confidence, newest first, hydrated.

    Active = neither cleared (real end signal) nor expired (reported-only event aged
    out of the live view). Both kinds of ended events stay in the archive forever.
    """
    sql = (
        f"SELECT *, {_CONFIDENCE_SQL} AS confidence FROM events"
        f" WHERE cleared_at IS NULL AND expired_at IS NULL AND {_CONFIDENCE_SQL} >= ?"
    )
    params: list = [min_confidence]
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY detected_at DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    with get_connection() as conn:
        events = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return _hydrate(conn, events)


def get_event(event_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT *, {_CONFIDENCE_SQL} AS confidence FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return _hydrate(conn, [dict(row)])[0]


def _hydrate(conn, events: list[dict]) -> list[dict]:
    """Parse JSON columns, join typed source records, and stamp read-time freshness."""
    now = datetime.now(timezone.utc)
    for e in events:
        e["lines"] = json.loads(e["lines"] or "[]")
        # Freshness is computed at read, never stored (the v1 ingest-time recency was a
        # dead constant). The frontend maps age to opacity/ordering; verification is
        # untouched by age.
        detected = datetime.fromisoformat(e["detected_at"])
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        e["age_minutes"] = max(0, int((now - detected).total_seconds() // 60))
    ids = [e["id"] for e in events]
    if not ids:
        return events
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT event_id, source_type, source_id, first_seen_at, last_seen_at, published_at"
        f" FROM event_sources WHERE event_id IN ({marks}) ORDER BY first_seen_at",
        ids,
    ).fetchall()
    by_event: dict[str, list[dict]] = {}
    for r in rows:
        by_event.setdefault(r["event_id"], []).append(
            {"type": r["source_type"], "id": r["source_id"],
             "first_seen_at": r["first_seen_at"], "last_seen_at": r["last_seen_at"],
             "published_at": r["published_at"]}
        )
    for e in events:
        e["sources"] = by_event.get(e["id"], [])
    return events


# ---------------------------------------------------------------- event_sources

def link_source(event_id: str, source_type: str, source_id: str, seen_at: str,
                last_hash: str, published_at: str | None = None, conn=None):
    """Attach a source item to an event; re-linking preserves first_seen_at and any
    earlier published_at (the earliest source timestamp is the latency-relevant one)."""
    sql = """
        INSERT INTO event_sources
            (event_id, source_type, source_id, first_seen_at, last_seen_at, last_hash, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, source_id) DO UPDATE SET
            event_id     = excluded.event_id,
            last_seen_at = excluded.last_seen_at,
            last_hash    = excluded.last_hash,
            published_at = COALESCE(event_sources.published_at, excluded.published_at)
    """
    args = (event_id, source_type, source_id, seen_at, seen_at, last_hash, published_at)
    if conn is not None:
        conn.execute(sql, args)
        return
    with get_connection() as conn:
        conn.execute(sql, args)


def touch_sources_seen(source_type: str, source_ids: list[str], seen_at: str):
    """Items still present in a successfully polled feed keep their events alive —
    last_seen_at is the substrate clearance detection (step 0.3) reads."""
    if not source_ids:
        return
    marks = ",".join("?" * len(source_ids))
    with get_connection() as conn:
        conn.execute(
            f"UPDATE event_sources SET last_seen_at = ? WHERE source_type = ? AND source_id IN ({marks})",
            [seen_at, source_type, *source_ids],
        )


def known_source_hashes(source_type: str) -> dict[str, str]:
    """source_id -> last processed content hash, per source type (ids from different
    feeds can collide — a global map would silently mask events). This is the dedup
    input: hash match = unchanged, mismatch = the item re-enters as an update."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT source_id, last_hash FROM event_sources WHERE source_type = ?",
            (source_type,),
        ).fetchall()
    return {r["source_id"]: r["last_hash"] for r in rows}


def find_event_id_by_source(source_type: str, source_id: str) -> str | None:
    """Which event does this source item feed? The update-routing lookup."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT event_id FROM event_sources WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        ).fetchone()
    return row["event_id"] if row else None


def mark_source_content(source_type: str, source_id: str, chash: str, seen_at: str):
    """Acknowledge a changed item's new content (last_hash + last_seen_at) without
    touching its event link. Called after a successful extraction call even when the
    item yielded no event — otherwise it would re-extract every cycle forever."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE event_sources SET last_seen_at = ?, last_hash = ?"
            " WHERE source_type = ? AND source_id = ?",
            (seen_at, chash, source_type, source_id),
        )


def get_active_source_types() -> dict[str, set[str]]:
    """event_id -> set of source types, for active events (corroboration matching)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT es.event_id, es.source_type FROM event_sources es"
            " JOIN events e ON e.id = es.event_id WHERE e.cleared_at IS NULL"
        ).fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["event_id"], set()).add(r["source_type"])
    return out


# ---------------------------------------------------------------- lifecycle

def record_poll(source_type: str, polled_at: str, items: int):
    """Log a SUCCESSFUL poll. Only callers that completed a fetch may call this —
    poll_log is what makes 'vanished' provable and a dead feed unable to clear events."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO poll_log (source_type, polled_at, items) VALUES (?, ?, ?)",
            (source_type, polled_at, items),
        )


def recent_poll_times(source_type: str, limit: int = 10) -> list[str]:
    """Most recent successful poll timestamps for a source, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT polled_at FROM poll_log WHERE source_type = ?"
            " ORDER BY polled_at DESC LIMIT ?",
            (source_type, limit),
        ).fetchall()
    return [r["polled_at"] for r in rows]


def set_event_cleared(event_id: str, cleared_at: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE events SET cleared_at = ?, updated_at = ? WHERE id = ?",
            (cleared_at, cleared_at, event_id),
        )


def set_event_expired(event_id: str, expired_at: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE events SET expired_at = ?, updated_at = ? WHERE id = ?",
            (expired_at, expired_at, event_id),
        )


# ---------------------------------------------------------------- raw_items

def archive_raw_item(source_type: str, source_id: str, chash: str,
                     payload_json: str, fetched_at: str) -> bool:
    """Archive a fetched item. Returns True if this (id, content) pair is new.
    A changed item gets a new row — history accumulates, nothing is overwritten."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_items"
            " (source_type, source_id, content_hash, fetched_at, payload)"
            " VALUES (?, ?, ?, ?, ?)",
            (source_type, source_id, chash, fetched_at, payload_json),
        )
        return cur.rowcount == 1


def update_raw_extraction(source_type: str, source_id: str, chash: str | None,
                          extraction_json: str):
    """Record what Claude returned for a raw item (the /review eval pairing)."""
    if chash is None:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE raw_items SET extraction = ?"
            " WHERE source_type = ? AND source_id = ? AND content_hash = ?",
            (extraction_json, source_type, source_id, chash),
        )


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
